#!/usr/bin/env python3
"""치환 맵 사전검증기 — replace 를 돌리기 전에 '안 맞을 키'를 잡아낸다.

왜 필요한가 (실측 근거, gyehoek-reference.hwpx 기준):
  · 문서가 '‧'(U+2027) 89회와 '·'(U+00B7) 86회를 섞어 쓴다. 눈으로 구별
    불가라, 사람이 텍스트를 보고 키를 타이핑하면 약 50% 확률로 빗나간다.
  · 28개 문단에 <hp:fwSpace/> <hp:tab/> <hp:lineBreak/> 같은 인라인 컨트롤이
    있고, 그중 2개는 <hp:t> 사이에 끼어 문구를 쪼갠다. fill_hwpx 의 매처는
    <hp:t> 텍스트만 이어붙이므로 그 경계를 넘는 키는 조용히 실패한다.
  두 경우 모두 예외 없이 'not_found' 로만 조용히 보고되어, 결과물에 원본
  문구가 남은 채로 배포되는 사고가 난다.

사용법:
  # 1) 매처와 '완전히 같은 방식'으로 문단 텍스트를 뽑는다 (키 원천)
  map_preflight.py dump  <file.hwpx> [--grep 검색어]
      출력 형식: <번호> TAB <OK|CTRL> TAB <문단텍스트>
      → 마지막 탭 뒤 전체가 키다. 앞 두 컬럼은 메타데이터이니 키에 넣지 마라.

  # 2) 맵을 돌리기 전에 검증한다. 못 맞추는 키는 교정안을 제시한다
  map_preflight.py check <file.hwpx> --map map.json [--fix out.json]

종료코드: 0=전부 매칭, 2=문제 있음(=replace 돌리지 말 것)
"""
import argparse, json, re, sys, zipfile, unicodedata

T_RE    = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
INNER_RE = re.compile(r"<[^>]*>")   # fill_hwpx 의 _INNER_TAG_RE 와 동일
ENT_RE  = re.compile(r"&(#x[0-9A-Fa-f]+|#\d+|\w+);")
ENTS    = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
CTRL_RE = re.compile(r"<hp:(fwSpace|tab|lineBreak)\b")

# 시각적으로 구별이 안 되는 문자들을 한 대표문자로 접는다(비교 전용).
FOLD = {}
for src, dst in (("·‧・•∙⋅", "·"), ("‘’‛`´", "'"), ('“”„', '"'),
                 ("~∼〜～", "~"), ("-–—―‐‑", "-"), ("　    ", " ")):
    for ch in src:
        FOLD[ch] = dst


def decode(s):
    def rep(m):
        e = m.group(1)
        if e[:2].lower() == "#x":
            return chr(int(e[2:], 16))
        if e[0] == "#":
            return chr(int(e[1:]))
        return ENTS.get(e, m.group(0))
    return ENT_RE.sub(rep, s)


def is_pua(c):
    """한컴 글머리 글리프(사설영역). 텍스트로 옮길 때 유실되기 쉬워 비교에서 제외."""
    o = ord(c)
    return 0xE000 <= o <= 0xF8FF or 0xF0000 <= o <= 0x10FFFD


def fold(s):
    """비교용 정규화: 혼동문자 접기 + 공백 제거 + 글머리 글리프 제거."""
    s = unicodedata.normalize("NFC", s)
    return "".join(FOLD.get(c, c) for c in s
                   if not c.isspace() and not is_pua(c))


def paragraphs(xml):
    """fill_hwpx 의 own_tnodes 와 같은 단위로 (문단텍스트, 컨트롤유무) 산출.

    각 <hp:p> 에 '직접' 속한 <hp:t> 만 이어붙인다 — 중첩 문단 소속은 제외.
    """
    out = []
    for m in re.finditer(r"<hp:p\b", xml):
        st = m.start()
        depth, en = 0, None
        for t in re.finditer(r"<hp:p\b|</hp:p>", xml[st:]):
            depth += 1 if t.group().startswith("<hp:p") else -1
            if depth == 0:
                en = st + t.end()
                break
        if en is None:
            continue
        frag = xml[st:en]
        if len(re.findall(r"<hp:p\b", frag)) != 1:      # 중첩 컨테이너는 건너뜀
            continue
        # fill_hwpx TNode 와 동일 순서: 내부 태그 제거 → 엔티티 디코딩.
        # <hp:t> 안의 <hp:fwSpace/> 는 공백조차 남기지 않고 사라진다.
        text = "".join(decode(INNER_RE.sub("", t)) for t in T_RE.findall(frag))
        if not text.strip():
            continue
        # <hp:t> 사이에 낀 컨트롤 = 문구를 쪼개는 컨트롤
        seq = [x.group(1) for x in re.finditer(r"<hp:(t|fwSpace|tab|lineBreak)\b", frag)]
        split = any(seq[i] != "t" and seq[i-1] == "t" and seq[i+1] == "t"
                    for i in range(1, len(seq) - 1))
        out.append((text, split))
    return out


def load_paras(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if re.fullmatch(r"Contents/section\d+\.xml", n)]
        paras = []
        for n in sorted(names):
            paras += paragraphs(z.read(n).decode("utf-8"))
    return paras


def cmd_dump(a):
    paras = load_paras(a.file)
    for i, (t, split) in enumerate(paras):
        if a.grep and fold(a.grep) not in fold(t):
            continue
        flag = "CTRL" if split else "OK"
        # 주석은 반드시 텍스트 '앞'에 둔다. 뒤에 붙이면 키로 복사될 때
        # 주석까지 딸려 들어가 매칭이 깨진다(실사용에서 발생).
        print(f"{i}\t{flag}\t{t}")
    return 0


def find_span(fk, folded):
    """키가 연속된 여러 문단에 걸쳐 있는지 — 즉 문단들을 하나로 합쳐 쓴 키인지."""
    n = len(folded)
    for i in range(n):
        if not fk.startswith(folded[i][0][:12] or "\0"):
            continue
        acc = ""
        for j in range(i, min(i + 12, n)):
            acc += folded[j][0]
            if acc == fk:
                return [folded[k][1] for k in range(i, j + 1)]
            if not fk.startswith(acc):
                break
    return None


def cmd_check(a):
    paras = load_paras(a.file)
    joined = "\n".join(t for t, _ in paras)
    folded = [(fold(t), t, split) for t, split in paras]
    mapping = json.load(open(a.map, encoding="utf-8"))

    ok, fixable, missing, multi = [], [], [], []
    for key, val in mapping.items():
        if key in joined:                       # 매처가 실제로 찾을 수 있음
            ok.append(key)
            continue
        fk = fold(key)
        cands = [(t, split) for f, t, split in folded if fk and fk in f]
        if cands:
            fixable.append((key, val, cands[0][0], cands[0][1]))
            continue
        span = find_span(fk, folded)
        if span:
            multi.append((key, val, span))
        else:
            missing.append(key)

    fixed = None
    if fixable and a.fix:
        fixed = dict(mapping)
        for key, val, para, _ in fixable:
            # 문단 전체를 키로 삼는다 — 부분 추출은 다시 어긋날 수 있다
            fixed.pop(key, None)
            fixed[para] = val
        json.dump(fixed, open(a.fix, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    print(f"총 {len(mapping)}개 키 — 정상 {len(ok)} / 교정필요 {len(fixable)} / "
          f"문단합침 {len(multi)} / 미발견 {len(missing)}")
    for key, val, para, split in fixable:
        why = "인라인 컨트롤이 문구를 쪼갬" if split else "혼동문자(·/‧, 따옴표, 물결 등) 불일치"
        print(f"\n[교정필요] {why}")
        print(f"  준 키   : {key}")
        print(f"  실제 원문: {para}")
        if split:
            print("   ※ 이 문단은 <hp:t> 사이에 컨트롤이 끼어 있어, 매처가 보는 텍스트는")
            print("     위 '실제 원문' 그대로다. 반드시 이 문자열을 키로 써라.")
    for key, val, span in multi:
        print(f"\n[문단합침] 이 키는 문서에서 {len(span)}개의 '별개 문단'이다.")
        print(f"  준 키   : {key[:70]}...")
        print( "  => 아래처럼 문단별로 쪼개서 각각 키로 등록해라:")
        for s_ in span:
            print(f"     · {s_}")
    for key in missing:
        print(f"\n[미발견] 문서에 이런 문구가 없다 (오타/다른 문서/이미 치환됨): {key}")
    if fixed is not None:
        print(f"\n교정된 맵을 저장했다: {a.fix}  (교정 {len(fixable)}건)")

    if fixable or missing or multi:
        print("\n=> replace 를 돌리지 마라. 위 키를 고친 뒤 다시 check 해서 '전부 정상'을 확인할 것.")
        return 2
    print("=> 전부 매칭. replace 안전.")
    return 0


def cmd_residue(a):
    """산출물에 원본(레퍼런스) 문단이 그대로 남았는지 전수 대조.

    preflight 는 '내가 쓴 키'만 검증한다. 정작 흔한 사고는 맵에 아예 넣지
    않은 문단에 원본 내용이 남는 것이라, 원본과 직접 대조해야만 잡힌다.
    """
    import collections
    out_list = [t for t, _ in load_paras(a.file)]
    out_cnt = collections.Counter(out_list)
    out = set(out_list)
    ref = [t for t, _ in load_paras(a.against)]
    ref_set = set(ref)
    left = sorted(out & ref_set, key=lambda t: (-len(t), t))

    # 순수 기호/숫자/한 글자짜리는 어느 문서에나 있는 범용 조각이라 제외
    def meaningful(t):
        core = "".join(c for c in t if not c.isspace())
        return len(core) >= a.min_chars and any(
            "\uac00" <= c <= "\ud7a3" or c.isalpha() for c in core)

    left = [t for t in left if meaningful(t)]
    if a.ignore:
        pats = [fold(x) for x in a.ignore]
        left = [t for t in left if not any(p in fold(t) for p in pats)]

    print(f"산출물 문단 {len(out)}개 / 레퍼런스 문단 {len(ref_set)}개")
    print(f"원본 그대로 남은 문단: {len(left)}개 (최소 {a.min_chars}자 기준)")
    for t in left:
        n = out_cnt[t]
        mark = f"  (x{n})" if n > 1 else ""
        print(f"  · {t}{mark}")
    if left:
        print("\n=> 위 문단들은 레퍼런스와 글자까지 동일하다. 의도한 범용 라벨이면"
              "\n   --ignore 로 제외하고, 아니면 맵에 추가해 치환하라.")
        return 2
    print("=> 원본 잔재 없음.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="치환 맵 사전검증기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="매처와 동일한 방식으로 문단 텍스트 덤프")
    d.add_argument("file"); d.add_argument("--grep")
    d.set_defaults(fn=cmd_dump)
    c = sub.add_parser("check", help="맵 사전검증 + 교정안 제시")
    c.add_argument("file"); c.add_argument("--map", required=True)
    c.add_argument("--fix", help="교정된 맵을 이 경로에 저장")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("residue", help="산출물에 원본 문단이 그대로 남았는지 대조")
    r.add_argument("file"); r.add_argument("--against", required=True,
                                           help="원본 레퍼런스 hwpx")
    r.add_argument("--min-chars", type=int, default=8, dest="min_chars")
    r.add_argument("--ignore", nargs="*", default=[],
                   help="의도적으로 유지하는 범용 라벨(부분일치)")
    r.set_defaults(fn=cmd_residue)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
