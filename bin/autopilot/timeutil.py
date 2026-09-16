"""Epoch helpers. Everything stored is epoch seconds; local time is for display only."""
import datetime
import math
import time


def now():
    return int(time.time())


def now_ms():
    return int(time.time() * 1000)


def parse_iso(value):
    """ISO-8601 with an explicit offset or Z -> epoch float; None when it is not one."""
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        result = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ceil_epoch(value):
    return int(math.ceil(value))


def round_epoch(value):
    """Nearest whole second, so "23:00:00.16+00:00" and "23:00:00+00:00" name the same reset."""
    return int(math.floor(value + 0.5))


def next_midnight(zone, now_epoch):
    """The next 00:00 wall-clock time in the IANA zone, strictly after now_epoch."""
    import zoneinfo

    tz = zoneinfo.ZoneInfo(zone)
    local = datetime.datetime.fromtimestamp(now_epoch, tz)
    tomorrow = local.date() + datetime.timedelta(days=1)
    midnight = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=tz, fold=0)
    return int(midnight.timestamp())


def local_hm(epoch, now_epoch):
    """"10:10" when epoch is on today's local date, else "Sat 18:01"."""
    target = time.localtime(epoch)
    today = time.localtime(now_epoch)
    if (target.tm_year, target.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", target)
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return "%s %s" % (days[target.tm_wday], time.strftime("%H:%M", target))


def duration_text(seconds):
    """"41s", "14m", "2h 5m", "2d 3h"."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        hours, minutes = seconds // 3600, (seconds % 3600) // 60
        return "%dh %dm" % (hours, minutes) if minutes else "%dh" % hours
    days, hours = seconds // 86400, (seconds % 86400) // 3600
    return "%dd %dh" % (days, hours) if hours else "%dd" % days
