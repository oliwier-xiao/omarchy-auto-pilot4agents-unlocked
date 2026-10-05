"""Find folders by name anywhere in the home folder, for people who do not type paths.

The picker sends the words someone typed ("projects", "last man") and gets back the folders that
best match them, so nobody has to know that ~/Projects is spelled with a ~.

The walk is bounded and reads names only:

  - it starts at the home folder and goes down breadth first, at most FIND_DEPTH levels, so the
    folders a cap leaves out are always the deepest ones;
  - every folder is opened component by component from the home folder with O_DIRECTORY |
    O_NOFOLLOW, so a link is never followed and a folder swapped for a link is never entered;
  - hidden folders (a name starting with "."), other file systems mounted under the home folder,
    and names that are not printable UTF-8 are never entered; folders such as node_modules or
    build, and Go's module cache (pkg/mod), are listed by name but never entered, since what is
    inside them is not where people work;
  - at most FIND_ENTRIES directory entries and FIND_FOLDERS folders within FIND_DEADLINE_S, after
    which the answer says it stopped early;
  - nothing is opened or read inside a folder: the folders that are answered get one fstat (whose
    folder it is) and one lstat of their .git entry (whether it is a git checkout).

Only the best FIND_LIMIT folders are answered. Nothing is written and nothing is kept between
calls.

Matching is case- and accent-insensitive (NFKD without combining marks, plus ł, đ, ø and ß, which
NFKD keeps). Every word must match the folder's name or one of the folders above it, and at least
one must match the name itself. A word scores by tier, best first: the whole name, the start of
the name, the start of a later word in the name, anywhere in the name, its letters in order
(scored the way fzf scores them), then a name a letter or two off. Shallow folders, short names,
git checkouts and folders the picker already knows sessions in rank higher within a tier, and a
folder inside a git checkout ranks below the checkout itself. Once a name matches outright, the
loose matches (letters in order, a letter off) are left out.
"""

import functools
import os
import stat
import time
import unicodedata

from . import consts, folders, proto

_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_SEPARATORS = frozenset(" -_.")
_FOLD_EXTRA = {"ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "ø": "o", "Ø": "o", "ß": "ss", "ı": "i"}

# Score tiers for one word against one name. A tier always beats the one below it; the
# bonuses added on top (git, sessions, depth, length) stay smaller than the gap between tiers.
_EXACT, _PREFIX, _WORD, _INSIDE = 1000, 800, 600, 400
_INSIDE_FLOOR, _ORDER_MAX, _ORDER_MIN, _TYPO = 300, 290, 100, 200
_ANCESTOR = 0.35
_KNOWN_BONUS, _GIT_BONUS = 80, 15
# A folder inside a git checkout (below its root) is rarely where an agent should start.
_INNER_PENALTY = 150
# Below this a hit is noise; and when some hit matched a name outright (_STRONG), the loose
# tiers are left out.
_FLOOR, _STRONG, _LOOSE = 90, 600, 300


def fold(text):
    """(folded, where): text lower-cased without accents, and for every folded character the
    index of the character of text it came from (so a match can be marked in the original)."""
    if text.isascii():
        return text.lower(), list(range(len(text)))
    out, where = [], []
    for i, ch in enumerate(text):
        folded = _FOLD_EXTRA.get(ch)
        if folded is None:
            folded = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).casefold()
        for c in folded:
            out.append(c)
            where.append(i)
    return "".join(out), where


def query_words(query):
    """The folded words of a query: split on spaces, slashes and the separators names use."""
    folded, _where = fold(query)
    for sep in "/\\-_.":
        folded = folded.replace(sep, " ")
    return [word for word in folded.split() if word]


def _word_starts(name, folded, where):
    """Positions in folded where a word of name starts: after a separator, at a capital that
    follows a small letter, and where letters turn into digits or back."""
    starts = []
    for j in range(len(folded)):
        if j == 0:
            starts.append(j)
            continue
        prev, cur = folded[j - 1], folded[j]
        if cur in _SEPARATORS:
            continue
        if prev in _SEPARATORS:
            starts.append(j)
            continue
        a, b = name[where[j - 1]], name[where[j]]
        if (a.islower() and b.isupper()) or (a.isdigit() != b.isdigit() and a.isalnum() and b.isalnum()):
            starts.append(j)
    return starts


def _osa(a, b, limit):
    """Optimal string alignment distance (Levenshtein plus swapped neighbours), or limit + 1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def _in_order(word, folded, starts):
    """fzf-style score for word's letters appearing in order in folded, with their positions, or
    (0, None). A letter at a word start or right after the previous one scores more; gaps cost."""
    positions = []
    at = 0
    for ch in word:
        found = folded.find(ch, at)
        if found < 0:
            return 0, None
        positions.append(found)
        at = found + 1
    start_set = set(starts)
    # The letters have to start a word somewhere: "pr" scattered inside "spray" is no match.
    if positions[0] not in start_set:
        return 0, None
    score, last = 0, None
    for n, p in enumerate(positions):
        bonus = 8 if p in start_set else 0
        if n == 0:
            bonus *= 2
        elif p == last + 1:
            bonus += 4
        else:
            gap = p - last - 1
            score -= 3 + (gap - 1)
        score += 16 + bonus
        last = p
    return max(_ORDER_MIN, min(_ORDER_MAX, score)), [(p, p + 1) for p in positions]


@functools.lru_cache(maxsize=65536)
def _prepared(name):
    """A name folded once: (folded, where, word starts, folded without separators)."""
    folded, where = fold(name)
    return folded, where, _word_starts(name, folded, where), "".join(c for c in folded if c not in _SEPARATORS)


def score_word(word, name):
    """(score, marks) for one query word against one folder name; marks are [start, end) ranges
    of characters of name, empty when the match is not one that can be shown letter by letter."""
    folded, where, starts, compact = _prepared(name)

    def marks(spans):
        out = []
        for a, b in spans:
            if a < b <= len(where):
                out.append((where[a], where[b - 1] + 1))
        return out

    if folded == word:
        return _EXACT, marks([(0, len(word))])
    if folded.startswith(word):
        return _PREFIX, marks([(0, len(word))])
    for s in starts[1:]:
        if folded.startswith(word, s):
            return _WORD, marks([(s, s + len(word))])
    found = folded.find(word)
    if found >= 0:
        return max(_INSIDE_FLOOR, _INSIDE - 2 * found), marks([(found, found + len(word))])
    # "lastman" for Last-Man: the name with its separators taken out.
    if len(word) >= 3 and compact == word:
        return _EXACT - 100, []
    if len(word) >= 3 and compact.startswith(word):
        return _PREFIX - 100, []
    if len(word) >= 3 and word in compact:
        return _INSIDE_FLOOR, []
    if len(word) < 3:
        return 0, []
    score, spans = _in_order(word, folded, starts)
    if score:
        return score, marks(spans)
    if len(word) < 4:
        return 0, []
    limit = 1 if len(word) <= 7 else 2
    best = limit + 1
    best_span = None
    bounds = starts + [len(folded)]
    for k, s in enumerate(starts):
        end = next((b for b in bounds[k + 1:] if b > s), len(folded))
        piece = folded[s:end].strip(" -_.")
        # A typo keeps the first or the second letter: cheap to check, and it keeps a home with
        # thousands of folders inside the deadline.
        if not piece or (piece[0] != word[0] and piece[1:2] != word[1:2]):
            continue
        for candidate in (piece, piece[:len(word)]):
            d = _osa(word, candidate, limit)
            if d < best:
                best, best_span = d, (s, s + len(candidate))
    if best <= limit:
        return _TYPO - 60 * best, marks([best_span]) if best_span else []
    return 0, []


def score_folder(words, name, ancestors):
    """(score, marks) for a folder: every word must match its name or a folder above it, and at
    least one word its name. ancestors are the names above it, nearest last."""
    total, on_name, all_marks = 0.0, 0, []
    for word in words:
        score, marks = score_word(word, name)
        if score:
            on_name += 1
            total += score
            all_marks.extend(marks)
            continue
        above = max((score_word(word, up)[0] for up in ancestors), default=0)
        if not above:
            return 0, []
        total += _ANCESTOR * above
    if not on_name:
        return 0, []
    typed = sum(len(w) for w in words)
    total -= 0.5 * max(0, len(name) - typed)
    total -= 3 * len(ancestors)
    return total, sorted(set(all_marks))


def _open_from_home(home_fd, parts):
    """An fd for the folder parts below the home folder, each part opened without following a
    link, or None."""
    fd = os.dup(home_fd)
    try:
        for part in parts:
            nxt = os.open(part, _OPEN_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError:
        os.close(fd)
        return None
    return fd


def find_dirs(query, known=()):
    """Find answer (without "ok") for query: the best folders under the home folder by name.

    known are folders the picker already knows sessions in; they rank higher within a tier.
    """
    # One letter alone matches nearly every name: such words are left out.
    words = [w for w in query_words(query) if len(w) >= 2][:consts.FIND_WORDS_MAX]
    home = folders._home_real()
    known = frozenset(known)
    answer = {"home": home, "q": query, "entries": [], "truncated": False, "reason": None,
              "scanned": 0, "skipped": 0}
    if not words:
        return answer
    try:
        home_fd = os.open(home, _OPEN_FLAGS)
    except OSError:
        answer["reason"] = "denied"
        return answer
    uid = os.getuid()
    deadline = time.monotonic() + consts.FIND_DEADLINE_S
    hits = []
    entries = folders_seen = skipped = 0
    stop = None
    try:
        home_dev = os.fstat(home_fd).st_dev
        level = [((), False)]
        for depth in range(1, consts.FIND_DEPTH + 1):
            following = []
            for parts, in_repo in level:
                if stop:
                    break
                found, children, checkout = [], [], False
                fd = _open_from_home(home_fd, parts) if parts else os.dup(home_fd)
                if fd is None:
                    continue
                try:
                    if os.fstat(fd).st_dev != home_dev:
                        continue
                    with os.scandir(fd) as it:
                        for entry in it:
                            entries += 1
                            if entries > consts.FIND_ENTRIES:
                                stop = "entries"
                                break
                            if entries % 256 == 0 and time.monotonic() > deadline:
                                stop = "time"
                                break
                            if entry.name == ".git":
                                checkout = True
                                continue
                            if entry.name.startswith("."):
                                continue
                            try:
                                if not entry.is_dir(follow_symlinks=False):
                                    continue
                            except OSError:
                                continue
                            name = folders._clean_name(entry.name)
                            if name is None:
                                skipped += 1
                                continue
                            folders_seen += 1
                            if folders_seen > consts.FIND_FOLDERS:
                                stop = "folders"
                                break
                            score, marks = score_folder(words, name, parts)
                            if score > 0:
                                found.append([score, parts + (name,), marks])
                            if depth < consts.FIND_DEPTH and name not in consts.FIND_NOT_ENTERED \
                                    and not (parts[-1:] == ("pkg",) and name == "mod"):
                                children.append(parts + (name,))
                except OSError:
                    continue
                finally:
                    os.close(fd)
                # Below a checkout's root: ranked under the checkout, and so are their children.
                inner = in_repo or checkout
                for hit in found:
                    if inner:
                        hit[0] -= _INNER_PENALTY
                    hits.append(tuple(hit))
                following.extend((child, inner) for child in children)
            if stop or not following:
                break
            level = following
        rows = _rows(home, home_fd, hits, known, uid)
    finally:
        os.close(home_fd)
    answer.update({"entries": rows, "truncated": stop is not None, "reason": stop,
                   "scanned": folders_seen, "skipped": skipped})
    return _fit(answer)


def _rows(home, home_fd, hits, known, uid):
    """The best hits as answer rows, with whose folder each is and whether it is a git checkout."""
    ranked = []
    for score, parts, marks in hits:
        path = home + "/" + "/".join(parts)
        if folders._clean_path(path) is None:
            continue
        if path in known:
            score += _KNOWN_BONUS
        ranked.append((score, path, parts, marks))
    ranked.sort(key=lambda r: (-r[0], len(r[2]), r[1].casefold(), r[1]))
    if ranked and ranked[0][0] >= _STRONG:
        ranked = [r for r in ranked if r[0] >= _LOOSE]
    ranked = [r for r in ranked if r[0] >= _FLOOR]
    rows = []
    for score, path, parts, marks in ranked[:consts.FIND_LIMIT * 2]:
        fd = _open_from_home(home_fd, parts)
        if fd is None:
            continue
        try:
            info = os.fstat(fd)
            git = folders._git_at(".", fd)
        except OSError:
            continue
        finally:
            os.close(fd)
        if not stat.S_ISDIR(info.st_mode):
            continue
        rows.append({"path": path, "name": parts[-1], "depth": len(parts), "git": git,
                     "own": info.st_uid == uid, "score": int(round(score + (_GIT_BONUS if git else 0))),
                     "marks": [list(m) for m in marks]})
    rows.sort(key=lambda r: (-r["score"], r["depth"], r["path"].casefold(), r["path"]))
    return rows[:consts.FIND_LIMIT]


def _fit(answer):
    """Rows that would carry the answer past its output cap are cut, never the whole answer."""
    rows = answer["entries"]
    answer["entries"] = []
    room = consts.OUTPUT_CAP["find-dirs"] - folders._CAP_SLACK - len(proto.serialize(answer))
    kept = 0
    for row in rows:
        room -= len(proto.serialize(row)) + 1
        if room < 0:
            answer["truncated"] = True
            answer["reason"] = answer["reason"] or "cap"
            break
        kept += 1
    answer["entries"] = rows[:kept]
    return answer
