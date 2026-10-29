"""Runtime engine: integrates the heating index and manages readings."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import periods as per
from .const import (
    BACKFILL_MIN_GAP,
    CHECKPOINT_INTERVAL,
    CONF_A,
    CONF_AUTO_K,
    CONF_FIX_A,
    CONF_HISTORY,
    CONF_K,
    CONF_READING_DATE,
    CONF_READING_VALUE,
    CONF_SENSORS,
    DEFAULT_A,
    DEFAULT_AUTO_K,
    DEFAULT_FIX_A,
    DEFAULT_K,
    DOMAIN,
    SAVE_DELAY,
    STORAGE_VERSION,
    TICK_INTERVAL,
    signal_update,
)
from .model import (
    estimate,
    fit_a_k,
    fit_k,
    interpolate_checkpoints,
    period_overlap_segments,
)

_LOGGER = logging.getLogger(__name__)

# Tail of a downtime gap (after the last compiled statistics) that may be
# integrated with the current sensor values instead of being skipped.
MAX_TAIL_WITH_CURRENT = timedelta(minutes=15)


def parse_local_datetime(value: str | datetime) -> datetime:
    """Parse a datetime; naive values are interpreted in the HA time zone."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = dt_util.parse_datetime(str(value))
        if dt is None:
            raise ValueError(f"Invalid datetime: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(dt)


def _ts(iso: str) -> float:
    return dt_util.parse_datetime(iso).timestamp()


def _state_value(state: State | None) -> float:
    """Return the heating power of a state; unavailable/unknown/invalid -> 0."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return 0.0
    try:
        return max(float(state.state), 0.0)
    except (TypeError, ValueError):
        return 0.0


class GasMeterEngine:
    """Virtual gas meter state machine for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the engine."""
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.data: dict[str, Any] = {}
        self._values: dict[str, float] = {}
        self._unsubs: list[Callable[[], None]] = []
        self.backfill_report: dict[str, Any] = {}

    # ------------------------------------------------------------------ config
    @property
    def _conf(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def sensors(self) -> list[str]:
        """Return the selected heating power sensors."""
        return list(self._conf.get(CONF_SENSORS, []))

    @property
    def auto_k(self) -> bool:
        """Return True if k is refitted automatically on new readings."""
        return bool(self._conf.get(CONF_AUTO_K, DEFAULT_AUTO_K))

    @property
    def fix_a(self) -> bool:
        """Return True if a is kept fixed during refits."""
        return bool(self._conf.get(CONF_FIX_A, DEFAULT_FIX_A))

    # ------------------------------------------------------------------- state
    @property
    def a(self) -> float:
        """Daily base consumption (m3/day)."""
        return float(self.data["a"])

    @property
    def k(self) -> float:
        """Heating coefficient (m3 per %·h)."""
        return float(self.data["k"])

    @property
    def readings(self) -> list[dict[str, Any]]:
        """Return the readings history (oldest first)."""
        return self.data["readings"]

    @property
    def last_reading(self) -> dict[str, Any]:
        """Return the most recent reading."""
        return self.readings[-1]

    @property
    def last_reading_dt(self) -> datetime:
        """Return the datetime (UTC) of the most recent reading."""
        return dt_util.parse_datetime(self.last_reading["date"])

    @property
    def index_since(self) -> float:
        """Heating index (%·h) accumulated since the last reading."""
        return float(self.data["index_since"])

    @property
    def last_update(self) -> datetime:
        """Return the instant up to which the index has been integrated."""
        return dt_util.parse_datetime(self.data["last_update"])

    @property
    def days_since(self) -> float:
        """Days between the last reading and the last integration instant."""
        return max(
            (self.last_update - self.last_reading_dt).total_seconds() / 86400, 0.0
        )

    @property
    def consumption_since(self) -> float:
        """Estimated m3 since the last reading."""
        return estimate(self.a, self.k, self.days_since, self.index_since)

    @property
    def raw_total(self) -> float:
        """Unclamped estimate of the meter (last reading + estimate)."""
        return float(self.last_reading["value"]) + self.consumption_since

    @property
    def total(self) -> float:
        """Published, never decreasing, meter estimate."""
        return float(self.data["published_total"])

    @property
    def held_offset(self) -> float:
        """m3 by which the published total is above the raw estimate."""
        return max(self.total - self.raw_total, 0.0)

    @property
    def pending_reading(self) -> float | None:
        """Value typed in the 'new reading' number entity."""
        return self.data.get("pending_reading")

    # --------------------------------------------------------------- lifecycle
    async def async_load(self) -> None:
        """Load persisted data or initialize (and seed) on first setup."""
        stored = await self._store.async_load()
        if stored is None:
            self.data = self._initial_data()
            _LOGGER.info(
                "First setup: %d readings initialized, last reading %s = %s m3",
                len(self.readings),
                self.last_reading["date"],
                self.last_reading["value"],
            )
        else:
            self.data = stored
        # Added in 0.1.2: older stores start the period accumulators now.
        self.data.setdefault("periods", {})
        self._apply_options()

    def _initial_data(self) -> dict[str, Any]:
        conf = self._conf
        value = float(conf.get(CONF_READING_VALUE))
        when = parse_local_datetime(conf.get(CONF_READING_DATE))
        a = float(conf.get(CONF_A, DEFAULT_A))
        k = float(conf.get(CONF_K, DEFAULT_K))
        readings: list[dict[str, Any]] = []
        if conf.get(CONF_HISTORY):
            for date_s, val, index, excluded in conf.get(CONF_HISTORY) or ():
                r_dt = parse_local_datetime(date_s)
                if r_dt > when:
                    break
                readings.append(
                    self._make_reading(readings, r_dt, val, index, a=a, k=k)
                )
                readings[-1]["excluded"] = excluded
                readings[-1]["source"] = "history"
        last = readings[-1] if readings else None
        if last is None or _ts(last["date"]) != when.timestamp():
            # Configured reading not in the seed: its interval index is unknown.
            readings.append(self._make_reading(readings, when, value, None, a=a, k=k))
            readings[-1]["source"] = "config"
        elif float(last["value"]) != value:
            last["value"] = value
            if len(readings) > 1:
                last["m3"] = value - float(readings[-2]["value"])
        return {
            "a": a,
            "k": k,
            "opt_a": a,
            "opt_k": k,
            "readings": readings,
            "index_since": 0.0,
            "last_update": when.isoformat(),
            "checkpoints": [],
            "published_total": value,
            "pending_reading": None,
            "pending_reading_date": None,
            "last_fit": None,
            "periods": {},
        }

    @staticmethod
    def _make_reading(
        readings: list[dict[str, Any]],
        when: datetime,
        value: float,
        index: float | None,
        *,
        a: float,
        k: float,
    ) -> dict[str, Any]:
        reading: dict[str, Any] = {
            "date": when.isoformat(),
            "value": float(value),
            "index": index,
            "days": None,
            "m3": None,
            "excluded": index is None,
            "estimated_m3": None,
            "error_m3": None,
        }
        if readings:
            prev = readings[-1]
            days = (when - dt_util.parse_datetime(prev["date"])).total_seconds() / 86400
            reading["days"] = round(days, 6)
            reading["m3"] = float(value) - float(prev["value"])
            if index is not None:
                est = estimate(a, k, days, index)
                reading["estimated_m3"] = round(est, 4)
                reading["error_m3"] = round(est - reading["m3"], 4)
        else:
            reading["excluded"] = True
        return reading

    def _apply_options(self) -> None:
        """Apply a/k changed by the user in the options flow."""
        conf = self._conf
        opt_a = float(conf.get(CONF_A, self.data["a"]))
        opt_k = float(conf.get(CONF_K, self.data["k"]))
        if opt_a != self.data.get("opt_a"):
            _LOGGER.info("Coefficient a set from options: %s", opt_a)
            self.data["a"] = opt_a
            self.data["opt_a"] = opt_a
        if opt_k != self.data.get("opt_k"):
            _LOGGER.info("Coefficient k set from options: %s", opt_k)
            self.data["k"] = opt_k
            self.data["opt_k"] = opt_k

    async def async_start(self) -> None:
        """Backfill downtime, then start live accumulation."""
        now = dt_util.utcnow()
        self._values = {
            eid: _state_value(self.hass.states.get(eid)) for eid in self.sensors
        }
        gap = now - self.last_update
        if gap >= BACKFILL_MIN_GAP:
            await self._async_backfill(self.last_update, now)
        elif gap.total_seconds() > 0:
            self._integrate(now)
        if self.last_update < now:
            self.data["last_update"] = now.isoformat()
        self._update_published()

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, self.sensors, self._async_state_changed
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_tick, TICK_INTERVAL, name=f"{DOMAIN} tick"
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_on_stop
            )
        )
        self._schedule_save()

    async def async_stop(self) -> None:
        """Stop listeners and flush to storage."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._catch_up(dt_util.utcnow())
        await self._store.async_save(self.data)

    async def async_remove(self) -> None:
        """Remove persisted data."""
        await self._store.async_remove()

    async def _async_on_stop(self, _event: Event) -> None:
        self._catch_up(dt_util.utcnow())
        await self._store.async_save(self.data)

    # ------------------------------------------------------------- integration
    def _integrate(self, now: datetime) -> None:
        """Integrate the current values from last_update up to now."""
        dt_s = (now - self.last_update).total_seconds()
        if dt_s <= 0:
            return
        power = sum(self._values.get(eid, 0.0) for eid in self.sensors)
        d_index = power * dt_s / 3600.0
        per.add_increment(
            self.data["periods"], self.last_update, now, d_index, a=self.a, k=self.k
        )
        self.data["index_since"] = self.index_since + d_index
        self.data["last_update"] = now.isoformat()
        self._maybe_checkpoint(now)

    def _catch_up(self, now: datetime) -> None:
        """Apply sensor changes not yet seen by the listener, then integrate.

        The state machine is the source of truth: a sensor whose current value
        differs from the cached one changed at ``last_changed`` but its
        state_changed listener has not run yet (on older Home Assistant
        versions listeners are dispatched on a later loop iteration, so a
        timer callback can run first). Integrate the old values up to each
        change in chronological order, so nothing is attributed to the wrong
        value regardless of callback ordering.
        """
        pending: list[tuple[datetime, str, float]] = []
        for eid in self.sensors:
            state = self.hass.states.get(eid)
            value = _state_value(state)
            if value != self._values.get(eid, 0.0):
                changed = state.last_changed if state is not None else now
                pending.append((min(changed, now), eid, value))
        for changed, eid, value in sorted(pending):
            self._integrate(changed)
            self._values[eid] = value
        self._integrate(now)

    def _maybe_checkpoint(self, now: datetime) -> None:
        cps = self.data["checkpoints"]
        last_ts = cps[-1][0] if cps else self.last_reading_dt.timestamp()
        if now.timestamp() - last_ts >= CHECKPOINT_INTERVAL.total_seconds():
            cps.append([now.timestamp(), self.index_since])

    def _update_published(self) -> None:
        raw = self.raw_total
        if raw > self.total:
            self.data["published_total"] = raw

    @callback
    def _async_state_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._catch_up(dt_util.utcnow())
        self._refresh()

    @callback
    def _async_tick(self, now: datetime) -> None:
        self._catch_up(dt_util.utcnow())
        self._refresh()

    @callback
    def _refresh(self) -> None:
        self._update_published()
        self._schedule_save()
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self.data, SAVE_DELAY)

    # ----------------------------------------------------------------- backfill
    async def _async_fetch_statistics(
        self, start: datetime, end: datetime, period: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch mean statistics for the selected sensors (recorder executor)."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )

        return await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            set(self.sensors),
            period,
            None,
            {"mean"},
        )

    @staticmethod
    def _rows(stats: dict[str, list[dict[str, Any]]]) -> list[tuple[float, float]]:
        rows: list[tuple[float, float]] = []
        for sensor_rows in stats.values():
            for row in sensor_rows:
                start = row.get("start")
                if isinstance(start, datetime):
                    start = start.timestamp()
                if start is None or row.get("mean") is None:
                    continue
                rows.append((float(start), float(row["mean"])))
        return rows

    async def _async_backfill(self, start: datetime, end: datetime) -> None:
        """Add the index for [start, end) from recorder statistics."""
        start_ts, end_ts = start.timestamp(), end.timestamp()
        report: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "added_index": 0.0,
            "covered_until": None,
            "skipped_hours": 0.0,
        }
        segments: list[tuple[float, float, float]] = []
        covered_until = start_ts
        try:
            hour_start = start.replace(minute=0, second=0, microsecond=0)
            stats = await self._async_fetch_statistics(hour_start, end, "hour")
            hourly = self._rows(stats)
            segments += period_overlap_segments(hourly, start_ts, end_ts, 3600.0)
            if hourly:
                covered_until = max(
                    covered_until, min(max(r[0] for r in hourly) + 3600.0, end_ts)
                )
            if end_ts - covered_until > 0:
                tail_start = dt_util.utc_from_timestamp(covered_until)
                stats5 = await self._async_fetch_statistics(tail_start, end, "5minute")
                short = self._rows(stats5)
                segments += period_overlap_segments(short, covered_until, end_ts, 300.0)
                if short:
                    covered_until = max(
                        covered_until, min(max(r[0] for r in short) + 300.0, end_ts)
                    )
        except Exception:
            _LOGGER.warning(
                "Could not read recorder statistics to backfill %s -> %s; "
                "this period is skipped",
                start,
                end,
                exc_info=True,
            )
            segments = []
            covered_until = start_ts

        periods = self.data["periods"]
        if not segments and covered_until == start_ts:
            report["skipped_hours"] = (end_ts - start_ts) / 3600
            per.add_missing(periods, start, end)
            _LOGGER.warning(
                "No recorder statistics available for %s -> %s (%.1f h): "
                "heating index for this period is skipped",
                start,
                end,
                report["skipped_hours"],
            )
        else:
            # Add segments in time order: hourly checkpoints, and day/month
            # accumulators get each increment in its own local period.
            cursor = start
            for lo_ts, hi_ts, value in sorted(segments):
                lo = dt_util.utc_from_timestamp(lo_ts)
                hi = dt_util.utc_from_timestamp(hi_ts)
                if lo > cursor:  # hole inside covered data: index 0
                    per.add_increment(periods, cursor, lo, 0.0, a=self.a, k=self.k)
                per.add_increment(
                    periods, max(lo, cursor), hi, value, a=self.a, k=self.k
                )
                cursor = max(cursor, hi)
                self.data["index_since"] = self.index_since + value
                self.data["last_update"] = hi.isoformat()
                self._maybe_checkpoint(hi)
                report["added_index"] += value
            covered = dt_util.utc_from_timestamp(covered_until)
            if covered > cursor:
                per.add_increment(periods, cursor, covered, 0.0, a=self.a, k=self.k)
            tail = end_ts - covered_until
            if tail > 0 and tail <= MAX_TAIL_WITH_CURRENT.total_seconds():
                self.data["last_update"] = covered.isoformat()
                before = self.index_since
                self._integrate(end)
                report["added_index"] += self.index_since - before
            elif tail > 0:
                report["skipped_hours"] = tail / 3600
                per.add_missing(periods, covered, end)
                _LOGGER.warning(
                    "Recorder statistics end at %s: %.1f h of heating index "
                    "skipped up to %s",
                    covered,
                    tail / 3600,
                    end,
                )
            report["covered_until"] = covered.isoformat()
            _LOGGER.info(
                "Backfilled %.1f %%·h from recorder statistics for %s -> %s",
                report["added_index"],
                start,
                end,
            )
        self.data["last_update"] = end.isoformat()
        self.backfill_report = report

    # ----------------------------------------------------------------- readings
    def _find_reading(self, when: datetime) -> int:
        """Find a reading by exact datetime, or by local calendar day."""
        exact = [
            i
            for i, r in enumerate(self.readings)
            if abs(_ts(r["date"]) - when.timestamp()) < 60
        ]
        if exact:
            return exact[0]
        day = dt_util.as_local(when).date()
        same_day = [
            i
            for i, r in enumerate(self.readings)
            if dt_util.as_local(dt_util.parse_datetime(r["date"])).date() == day
        ]
        if len(same_day) == 1:
            return same_day[0]
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="reading_not_found",
            translation_placeholders={"date": str(dt_util.as_local(when))},
        )

    def register_reading(self, value: float, when: datetime | None = None) -> dict:
        """Close the current interval with a real meter reading."""
        now = dt_util.utcnow()
        when = dt_util.as_utc(when) if when else now
        last = self.last_reading
        last_dt = self.last_reading_dt
        self._validate_when(when, now)
        if value < float(last["value"]):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reading_too_low",
                translation_placeholders={
                    "value": f"{value:.3f}",
                    "last": f"{float(last['value']):.3f}",
                },
            )

        self._catch_up(now)
        cps = [(last_dt.timestamp(), 0.0)]
        cps += [(float(t), float(v)) for t, v in self.data["checkpoints"]]
        cps.append((now.timestamp(), self.index_since))
        index_at = min(interpolate_checkpoints(cps, when.timestamp()), self.index_since)

        reading = self._make_reading(
            self.readings, when, value, index_at, a=self.a, k=self.k
        )
        reading["excluded"] = False
        reading["source"] = "user"
        self.readings.append(reading)
        _LOGGER.info(
            "Reading %.3f m3 at %s: interval %.2f days, I=%.1f %%·h, "
            "real %.3f m3, estimated %.3f m3",
            value,
            when,
            reading["days"],
            index_at,
            reading["m3"],
            reading["estimated_m3"],
        )

        # Carry what was accumulated after the reading into the new interval.
        self.data["index_since"] = max(self.index_since - index_at, 0.0)
        self.data["checkpoints"] = [
            [t, v - index_at]
            for t, v in self.data["checkpoints"]
            if t > when.timestamp()
        ]
        if self.auto_k:
            self._refit()
        self.data["pending_reading"] = None
        self.data["pending_reading_date"] = None
        self._after_change()
        return reading

    def _validate_when(self, when: datetime, now: datetime) -> None:
        """Reject a reading date before the last reading or in the future."""
        last_dt = self.last_reading_dt
        if when <= last_dt:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reading_too_old",
                translation_placeholders={
                    "date": dt_util.as_local(when).strftime("%Y-%m-%d %H:%M"),
                    "last": dt_util.as_local(last_dt).strftime("%Y-%m-%d %H:%M"),
                },
            )
        if when > now + timedelta(minutes=1):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reading_in_future",
                translation_placeholders={
                    "date": dt_util.as_local(when).strftime("%Y-%m-%d %H:%M")
                },
            )

    def delete_reading(self, when: datetime) -> dict:
        """Delete a reading, merging its interval into the neighbour."""
        idx = self._find_reading(dt_util.as_utc(when))
        if len(self.readings) <= 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="cannot_delete_only"
            )
        self._catch_up(dt_util.utcnow())
        removed = self.readings.pop(idx)
        rem_index = removed.get("index")
        if idx == len(self.readings):
            # Removed the last reading: its interval rejoins the current one.
            add = float(rem_index or 0.0)
            if rem_index is None:
                _LOGGER.warning(
                    "Deleted reading had no index; current interval index may be low"
                )
            rem_ts = _ts(removed["date"])
            self.data["index_since"] = self.index_since + add
            self.data["checkpoints"] = [[rem_ts, add]] + [
                [t, v + add] for t, v in self.data["checkpoints"]
            ]
        elif idx == 0:
            nxt = self.readings[0]
            nxt.update(
                index=None,
                days=None,
                m3=None,
                excluded=True,
                estimated_m3=None,
                error_m3=None,
            )
        else:
            nxt = self.readings[idx]
            if rem_index is None or nxt.get("index") is None:
                new_index = None
            else:
                new_index = float(rem_index) + float(nxt["index"])
            rebuilt = self._make_reading(
                self.readings[:idx],
                dt_util.parse_datetime(nxt["date"]),
                nxt["value"],
                new_index,
                a=self.a,
                k=self.k,
            )
            rebuilt["excluded"] = bool(
                removed.get("excluded") or nxt.get("excluded") or new_index is None
            )
            rebuilt["source"] = nxt.get("source")
            self.readings[idx] = rebuilt
        _LOGGER.info("Deleted reading %s (%s m3)", removed["date"], removed["value"])
        if self.auto_k:
            self._refit()
        self._after_change()
        return removed

    def recalculate(self) -> dict[str, Any] | None:
        """Refit the coefficients now (regardless of the auto setting)."""
        result = self._refit()
        self._after_change()
        return result

    def _refit(self) -> dict[str, Any] | None:
        res = None
        if not self.fix_a:
            res = fit_a_k(self.readings)
            if res is None:
                _LOGGER.info("Joint a/k fit not possible, falling back to k only")
        if res is None:
            res = fit_k(self.readings, self.a)
        if res is None:
            _LOGGER.warning("Not enough heating data to refit coefficients")
            return None
        _LOGGER.info(
            "Refit (%s): a=%.4f k=%.6f on %d intervals, RMSE %.2f m3",
            "a+k" if res.joint else "k",
            res.a,
            res.k,
            res.n,
            res.rmse or 0.0,
        )
        self.data["a"] = res.a
        self.data["k"] = res.k
        self.data["last_fit"] = {
            "a": res.a,
            "k": res.k,
            "intervals": res.n,
            "rmse_m3": res.rmse,
            "joint": res.joint,
            "at": dt_util.utcnow().isoformat(),
        }
        return self.data["last_fit"]

    def period(self, kind: str) -> dict[str, Any] | None:
        """Return a day/month accumulator: day, prev_day, month, prev_month."""
        return self.data["periods"].get(kind)

    @property
    def pending_reading_date(self) -> datetime | None:
        """Datetime set in the 'Reading date' entity, if any."""
        value = self.data.get("pending_reading_date")
        return dt_util.parse_datetime(value) if value else None

    def set_pending_reading_date(self, value: datetime | None) -> None:
        """Validate and store the datetime set in the 'Reading date' entity."""
        if value is not None:
            value = dt_util.as_utc(value)
            self._validate_when(value, dt_util.utcnow())
        self.data["pending_reading_date"] = value.isoformat() if value else None
        self._refresh()

    def set_pending_reading(self, value: float | None) -> None:
        """Store the value typed in the number entity."""
        self.data["pending_reading"] = value
        self._refresh()

    @callback
    def _after_change(self) -> None:
        self._update_published()
        self._refresh()
