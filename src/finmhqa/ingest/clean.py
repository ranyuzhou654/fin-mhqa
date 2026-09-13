"""Conservative cleanup: standalone financial numbers are never page-number heuristics."""
import re

HEADER = re.compile(r"^.{0,30}20\d{2}\s*年半年度报告(?:全文)?.{0,10}$")
TOC = re.compile(r"[.．…]{4,}\s*\d+\s*$", re.M)
LEVELS = [r"^第[一二三四五六七八九十百]+节\s*\S", r"^[一二三四五六七八九十百]+、\s*\S",
          r"^[（(][一二三四五六七八九十百]+[）)]\s*\S", r"^\d{1,2}[.、](?!\d)\s*\S"]

def heading_level(line):
    line = line.strip()
    if len(line) > 60:
        return 0
    return next((i for i, pat in enumerate(LEVELS, 1) if re.match(pat, line)), 0)

def is_toc_page(text):
    return len(TOC.findall(text)) >= 8

def clean_text(text):
    lines = [line.strip() for line in text.replace('\u3000', ' ').replace('\xa0', ' ').splitlines()]
    out = []
    for line in lines:
        if not line or HEADER.match(line):
            continue
        # Keep number-only lines and paragraph boundaries; merging these loses evidence.
        if (out and not heading_level(line) and not heading_level(out[-1])
                and re.search(r'[\u4e00-\u9fff]$', out[-1])
                and re.match(r'^[\u4e00-\u9fff]', line)):
            out[-1] += line
        else:
            out.append(line)
    return '\n'.join(out)
