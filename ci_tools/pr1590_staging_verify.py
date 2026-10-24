"""Staging-only checks for #1590, excluded from the eventual upstream PR.

The baseline is the upstream master SHA used to construct the staging branch.
Run differential checks and performance comparisons on the same CI runner.
"""
import copy
import pickle
import random
import statistics
import subprocess
import sys
import timeit
import types
from datetime import datetime, timedelta

from dateutil import tz
import dateutil.rrule as new

BASE_SHA = "64914d9bcf987f0a6a8de77c3d07cc26f06185c9"
source = subprocess.check_output(
    ["git", "show", BASE_SHA + ":src/dateutil/rrule.py"], text=True
)
old = types.ModuleType("dateutil._rrule_1590_baseline")
old.__package__ = "dateutil"
old.__file__ = "<upstream master:src/dateutil/rrule.py>"
sys.modules[old.__name__] = old
exec(compile(source, old.__file__, "exec"), old.__dict__)

print("CI validation baseline:", BASE_SHA, flush=True)
print("CI validation candidate:", subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True).strip(), flush=True)

checks = 0
random_gen = random.Random(1590)


def equal(lhs, rhs, case):
    global checks
    checks += 1
    if lhs != rhs:
        raise AssertionError(
            "Behavior differs at %s: upstream=%r staging=%r" % (case, lhs, rhs)
        )


def result(action):
    try:
        return ("return", action())
    except Exception as exc:
        return ("exception", type(exc).__name__)


def rule(mod, shape, n, aware, cache):
    start = datetime(2024, 1, 1, 9,
                     tzinfo=tz.gettz("America/New_York") if aware else None)
    args = {"dtstart": start, "count": n, "cache": cache}
    if shape == "daily":
        return mod.rrule(mod.DAILY, **args)
    if shape == "weekly":
        return mod.rrule(mod.WEEKLY, byweekday=(mod.MO, mod.FR), **args)
    if shape == "monthly":
        return mod.rrule(mod.MONTHLY, bymonthday=(1, 15), **args)
    if shape == "hourly":
        return mod.rrule(mod.HOURLY, **args)
    if shape == "set":
        obj = mod.rruleset(cache=cache)
        obj.rrule(mod.rrule(mod.DAILY, **args))
        obj.exdate(start + timedelta(days=1))
        obj.exrule(mod.rrule(mod.DAILY, dtstart=start, interval=9,
                             count=max(1, n // 9), cache=cache))
        obj.rdate(start + timedelta(days=n + 3))
        return obj
    raise ValueError(shape)


for shape in ("daily", "weekly", "monthly", "hourly", "set"):
    for count in (0, 1, 5, 31, 32, 33, 34, 65, 140):
        for aware in (False, True):
            for cache_state in ("disabled", "partial", "complete"):
                cached = cache_state != "disabled"
                a = rule(old, shape, count, aware, cached)
                b = rule(new, shape, count, aware, cached)
                if cache_state == "partial":
                    next(iter(a), None)
                    next(iter(b), None)
                elif cache_state == "complete":
                    list(a)
                    list(b)
                dates = list(rule(old, shape, count, aware, False))
                if not dates:
                    continue
                idx = (0, 1, 30, 31, 32, 33, len(dates) // 2, len(dates) - 1)
                targets = [dates[i] for i in sorted(set(idx)) if i < len(dates)]
                targets += [dates[0] - timedelta(days=1),
                            dates[-1] + timedelta(days=1),
                            dates[len(dates) // 2] + timedelta(minutes=1)]
                targets += [dates[random_gen.randrange(len(dates))]
                            for _ in range(2)]
                for target in targets:
                    for inclusive in (False, True):
                        for method in ("before", "after"):
                            equal(
                                result(lambda: getattr(a, method)(target, inc=inclusive)),
                                result(lambda: getattr(b, method)(target, inc=inclusive)),
                                (shape, count, aware, cache_state, method, target, inclusive),
                            )
                        for take in (None, 0, 1, 3, 40, 1.0, 1.5, 2.5):
                            equal(
                                result(lambda: list(a.xafter(target, count=take, inc=inclusive))),
                                result(lambda: list(b.xafter(target, count=take, inc=inclusive))),
                                (shape, count, aware, cache_state, "xafter", target,
                                 inclusive, take),
                            )
                        for end in (dates[0], dates[-1],
                                    target - timedelta(days=2),
                                    target + timedelta(days=4)):
                            equal(
                                result(lambda: a.between(target, end, inc=inclusive)),
                                result(lambda: b.between(target, end, inc=inclusive)),
                                (shape, count, aware, cache_state, "between",
                                 target, end, inclusive),
                            )
print("DIFFERENTIAL: PASS (%d comparisons)" % checks, flush=True)

roundtrips = 0
for shape in ("daily", "set"):
    for state in ("empty", "partial", "complete"):
        original = rule(new, shape, 150, True, True)
        expected = list(rule(old, shape, 150, True, False))
        if state == "partial":
            next(iter(original))
        if state == "complete":
            list(original)
        for restore in (copy.deepcopy, lambda x: pickle.loads(pickle.dumps(x))):
            restored = restore(original)
            if restored._cache_complete or restored._cache != []:
                raise AssertionError("Restored cache not reset")
            equal(list(restored), expected, (shape, state, "roundtrip"))
            target = expected[len(expected) // 2]
            comparison = rule(old, shape, 150, True, True)
            list(comparison)
            for method in ("before", "after"):
                equal(getattr(restored, method)(target),
                      getattr(comparison, method)(target),
                      (shape, state, method))
            equal(restored.between(target, expected[-1]),
                  comparison.between(target, expected[-1]),
                  (shape, state, "between"))
            equal(list(restored.xafter(target, count=5)),
                  list(comparison.xafter(target, count=5)),
                  (shape, state, "xafter"))
            roundtrips += 1
print("PICKLE/DEEPCOPY: PASS (%d combinations)" % roundtrips, flush=True)


def microseconds(method, number):
    values = timeit.repeat(method, number=number, repeat=5)
    return statistics.median(values) / number * 1e6


start = datetime(1700, 1, 1)
for cache_size in (16, 200000):
    a = old.rrule(old.DAILY, dtstart=start, count=cache_size, cache=True)
    b = new.rrule(new.DAILY, dtstart=start, count=cache_size, cache=True)
    list(a)
    list(b)
    for position in (0, cache_size // 2):
        target = start + timedelta(days=position)
        operations = {
            "before": lambda instance: instance.before(target),
            "after": lambda instance: instance.after(target),
            "between": lambda instance: instance.between(
                target, target + timedelta(days=6)),
            "xafter5": lambda instance: list(instance.xafter(target, count=5)),
        }
        for label, operation in operations.items():
            baseline_us = microseconds(
                lambda operation=operation: operation(a),
                80 if cache_size > 100 else 2000,
            )
            candidate_us = microseconds(
                lambda operation=operation: operation(b), 10000
            )
            ratio = baseline_us / candidate_us
            print("BENCH size=%d index=%d query=%s upstream=%.4fus staging=%.4fus ratio=%.1fx" %
                  (cache_size, position, label, baseline_us, candidate_us, ratio),
                  flush=True)
            if cache_size == 200000 and position == cache_size // 2:
                if ratio < 100:
                    raise AssertionError("Deep-cache speedup below 100x for %s" % label)
print("PERFORMANCE: PASS (deep-cache speedups >=100x)", flush=True)
