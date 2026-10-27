"""Questions with numbered options, from the plain-text asking convention (agents/claude.md,
"Asking the user"):

    **Which sources should I add?** (pick any)
    1. **Apple Notes:** macOS asks once
    2. **Gmail:** nothing new to allow

"(pick one)" / "(pick any)" on the question line marks single / multi select ("parse": "marked");
"(pick one only)" / "(pick any only)" turns off the free-text Other. Unmarked messages fall back to
a heuristic ("parse": "heuristic"):
- a numbered list right under a paragraph that asks ("?", or "reply / pick / choose") is that
  question's options, unless its items are questions themselves;
- in a numbered list of questions, a nested numbered list under an item is that item's options;
- an option that is itself a question ("2. Is Maple a teammate or you?") stays its own question;
- a line ending in ":" counts as asking only with a choice word ("pick", "options", "one of", ...),
  so "Then make one call:" over a list of steps is not a question with options;
- a list whose items ask nothing and that isn't options (steps, a checklist) is one question;
- a short question line ("Reply with a number:") gets the nearest bold heading in front of it.
"""
import re

from . import Parser, register_parser

ITEM_RE = re.compile(r"^(\s*)(?:\*\*)?(\d{1,2})[.)](?:\*\*)?\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*•]\s")
PICK_RE = re.compile(r"\(\s*(?:pick|choose|select)\s+(one|any|one or more|several)(\s+only)?\s*\)", re.I)
ASKS_RE = re.compile(r"\?|\b(reply|answer|pick|choose|select|options?|choices?|either|one of|prefer)\b", re.I)
HEADING_RE = re.compile(r"^\s*\*\*(.+?)\*\*")
REPLY_N_RE = re.compile(r"\s*\((?:reply|answer|type|say)\s+\d+\)", re.I)
MULTI_RE = re.compile(r"\b(numbers|any of|all that apply|one or more|as many|which ones)\b|"
                      r"e\.g\.,?\s*[\"“']?\d+\s*,\s*\d+", re.I)


def indent(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def clean(s):
    return re.sub(r"\s+", " ", s.replace("**", "")).strip()


def option(n, raw):
    raw = REPLY_N_RE.sub("", raw)
    m = re.match(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$", raw.strip())
    if m:
        label, detail = m.group(1), m.group(2)
    else:
        label, _, detail = raw.partition(": ") if 0 < raw.find(": ") <= 40 else (raw, "", "")
    return {"n": n, "label": clean(label).rstrip(":").rstrip(".") if not detail else clean(label).rstrip(":"),
            "detail": clean(detail)}


def blocks(body):
    """Split into paragraphs and top-level numbered lists, in order. A list item keeps its
    continuation lines and its nested numbered items ("sub"); numbering from 1 again starts a new list."""
    out, para, lst = [], [], None
    for line in body.split("\n"):
        m = ITEM_RE.match(line)
        if lst is not None:
            item = lst["items"][-1]
            if m and indent(line) <= lst["indent"] and int(m.group(2)) > item["n"]:
                lst["items"].append({"n": int(m.group(2)), "text": m.group(3), "sub": []})
                continue
            if not line.strip():
                lst["blank"] = True
                continue
            if indent(line) > lst["indent"]:
                lst["blank"] = False
                if m:
                    item["sub"].append([int(m.group(2)), m.group(3), indent(line)])
                elif item["sub"] and indent(line) > item["sub"][-1][2]:
                    item["sub"][-1][1] += " " + line.strip()
                elif not BULLET_RE.match(line) or len(item["text"]) < 200:
                    item["text"] += " " + line.strip()
                continue
            out.append(lst)
            lst = None
        if m:
            if para:
                out.append({"para": para})
            para = []
            lst = {"indent": indent(line), "items": [{"n": int(m.group(2)), "text": m.group(3), "sub": []}]}
        elif not line.strip():
            if para:
                out.append({"para": para})
            para = []
        else:
            para.append(line)
    if lst is not None:
        out.append(lst)
    if para:
        out.append({"para": para})
    return out


def is_question(text):
    return "?" in text.replace("**", "")


def mode(text):
    """(multi, allow_other, parse) from a marker in text, else the heuristic."""
    m = PICK_RE.search(text)
    if m:
        return m.group(1).lower() != "one", not m.group(2), "marked"
    return bool(MULTI_RE.search(text)), True, "heuristic"


def question(text, opts):
    """One question; options that are questions themselves are split out after it."""
    multi, other, how = mode(text)
    keep = [o for o in opts if not is_question(o[1])] if len(opts) > 1 else opts
    if len(keep) < len(opts) and not keep:
        keep = opts
    split = [o for o in opts if o not in keep]
    q = {"text": clean(PICK_RE.sub("", text)), "options": [option(n, t) for n, t in keep],
         "multi": multi if keep else False, "allow_other": other, "parse": how if keep else None}
    return [q] + [{"text": clean(t), "options": [], "multi": False, "allow_other": True, "parse": None}
                  for _, t in split]


@register_parser
class Numbered(Parser):
    name = "numbered"
    priority = 10

    def parse(self, body):
        bs = blocks(body)
        lists = [i for i, b in enumerate(bs) if "items" in b]
        out = []
        for i in lists:
            items = bs[i]["items"]
            ctx = "\n".join(bs[i - 1]["para"]) if i > 0 and "para" in bs[i - 1] else ""
            last_line = ctx.strip().splitlines()[-1] if ctx.strip() else ""
            asked = sum(is_question(it["text"]) for it in items)
            of_questions = asked and asked * 2 >= len(items)
            options = PICK_RE.search(last_line) or (not of_questions and ctx and ASKS_RE.search(ctx))
            if options:
                text = ctx
                if "?" not in ctx and "**" not in ctx and len(ctx) < 60:
                    head = next((HEADING_RE.match(ln).group(1) for b in reversed(bs[:i - 1]) if "para" in b
                                 for ln in reversed(b["para"]) if HEADING_RE.match(ln)), "")
                    text = f"{head.rstrip(' .:')}. {ctx}" if head else ctx
                out += question(text, [(it["n"], it["text"]) for it in items])
            elif i == lists[-1] and not asked and not any(it["sub"] for it in items):
                # steps or a checklist: one question that keeps them
                out += question(" ".join([ctx] + [f'{it["n"]}. {it["text"]}' for it in items]), [])
            elif i == lists[-1]:
                for it in items:
                    out += question(it["text"], [(n, t) for n, t, _ in it["sub"]])
        return out
