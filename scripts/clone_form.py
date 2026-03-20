#!/usr/bin/env python3
"""
HWPX 양식 복제 도구 (Workflow F)

기존 HWPX 양식을 복사한 뒤 텍스트만 치환하여 새 문서를 생성한다.
원본의 테이블·이미지·스타일을 100% 유지하면서 내용만 교체한다.

2단계 치환:
  Phase 1 — 구문 수준(--map/--replace): 전체 XML에서 긴 문구를 먼저 치환
  Phase 2 — 키워드 수준(--keywords): <hp:t> 태그 내부에서만 남은 키워드를 치환

사용법:
  분석:    python clone_form.py --analyze sample.hwpx
  복제:    python clone_form.py sample.hwpx output.hwpx --map map.json
  키워드:  python clone_form.py sample.hwpx output.hwpx --map map.json --keywords kw.json
  CLI:     python clone_form.py sample.hwpx output.hwpx --replace "원본=대체" "A=B"

Import:
  from clone_form import clone, analyze, extract_texts
"""

import argparse
import json
import os
import re
import sys
import zipfile


def extract_texts(hwpx_path):
    """HWPX에서 <hp:t> 태그의 텍스트를 모두 추출한다.

    Returns:
        list[str]: 고유 텍스트 목록 (등장 순서 유지)
    """
    texts = []
    seen = set()

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        for name in zf.namelist():
            if name.startswith("Contents/") and name.endswith(".xml"):
                data = zf.read(name).decode("utf-8")
                for m in re.finditer(r"<hp:t>(.*?)</hp:t>", data, re.DOTALL):
                    # 인라인 XML 태그 제거하여 순수 텍스트 추출
                    raw = m.group(1)
                    clean = re.sub(r"<[^>]+>", "", raw).strip()
                    if clean and clean not in seen:
                        seen.add(clean)
                        texts.append(clean)
    return texts


def analyze(hwpx_path):
    """HWPX 양식을 분석하여 구조 요약과 텍스트 목록을 출력한다."""
    print(f"=== HWPX 양식 분석: {hwpx_path} ===\n")

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        names = zf.namelist()
        print(f"ZIP 엔트리: {len(names)}개")

        # BinData 수
        bindata = [n for n in names if n.startswith("BinData/")]
        print(f"BinData (이미지 등): {len(bindata)}개")

        # section0.xml 분석
        if "Contents/section0.xml" in names:
            sec = zf.read("Contents/section0.xml").decode("utf-8")
            tables = len(re.findall(r"<hp:tbl ", sec))
            pics = len(re.findall(r"<hp:pic ", sec))
            paras = len(re.findall(r"<hp:p ", sec))
            runs = len(re.findall(r"<hp:run ", sec))
            print(f"문단: {paras}개, 런: {runs}개, 테이블: {tables}개, 이미지: {pics}개")
            print(f"section0.xml 크기: {len(sec):,} bytes")

    # 텍스트 추출
    texts = extract_texts(hwpx_path)
    print(f"\n고유 텍스트 조각: {len(texts)}개\n")
    for i, t in enumerate(texts, 1):
        display = t[:80] + "..." if len(t) > 80 else t
        print(f"  [{i:3d}] {display}")

    return texts


def auto_analyze(hwpx_path, output_json=None):
    """양식을 분석하고 치환 맵 템플릿을 JSON으로 출력한다.

    에이전트가 이 출력을 기반으로 치환 맵을 작성할 수 있도록
    원본 텍스트를 key로, 빈 문자열을 value로 하는 JSON을 생성한다.

    Args:
        hwpx_path: 분석할 .hwpx 파일
        output_json: 출력 JSON 경로 (None이면 stdout)

    Returns:
        dict: {structure: {...}, texts: [...], template: {...}}
    """
    structure = {}
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        names = zf.namelist()
        bindata = [n for n in names if n.startswith("BinData/")]
        structure["zip_entries"] = len(names)
        structure["bindata_count"] = len(bindata)

        if "Contents/section0.xml" in names:
            sec = zf.read("Contents/section0.xml").decode("utf-8")
            structure["tables"] = len(re.findall(r"<hp:tbl ", sec))
            structure["images"] = len(re.findall(r"<hp:pic ", sec))
            structure["paragraphs"] = len(re.findall(r"<hp:p ", sec))
            structure["runs"] = len(re.findall(r"<hp:run ", sec))
            structure["section_size"] = len(sec)

    texts = extract_texts(hwpx_path)

    # 워크플로우 추천
    has_tables = structure.get("tables", 0) > 0
    has_images = structure.get("images", 0) > 0
    if has_tables or has_images:
        recommendation = "Workflow F (clone_form.py) — 테이블/이미지 포함, 양식 복제 필수"
    else:
        recommendation = "Workflow C 또는 F 가능 — 단순 텍스트 문서"

    # 치환 맵 템플릿 생성
    template = {}
    for t in texts:
        if len(t) > 1:  # 1글자 이하 건너뜀
            template[t] = ""

    result = {
        "source": hwpx_path,
        "structure": structure,
        "recommendation": recommendation,
        "text_count": len(texts),
        "template_map": template,
    }

    output = json.dumps(result, ensure_ascii=False, indent=2)

    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"자동 분석 완료: {output_json}")
        print(f"  구조: 테이블 {structure.get('tables', 0)}개, "
              f"이미지 {structure.get('images', 0)}개, "
              f"문단 {structure.get('paragraphs', 0)}개")
        print(f"  텍스트 조각: {len(texts)}개")
        print(f"  추천: {recommendation}")
    else:
        print(output)

    return result


def _prepare_keywords(keywords):
    """키워드를 길이 내림차순으로 정렬한다 (긴 것이 먼저 매칭되도록)."""
    return sorted(keywords.items(), key=lambda x: len(x[0]), reverse=True)


def _apply_keywords_to_text(text, sorted_keywords):
    """순수 텍스트에 키워드 치환을 적용한다."""
    for old, new in sorted_keywords:
        if old in text:
            text = text.replace(old, new)
    return text


def _apply_keywords_in_xml(xml_text, sorted_keywords):
    """<hp:t> 태그 내부의 텍스트에만 키워드 치환을 적용한다.

    인라인 XML 요소(<hp:fwSpace/>, <hp:tab/> 등)가 키워드를
    분리하는 경우를 처리하기 위해 태그 경계에서 텍스트를 분할하여
    각 조각에 개별적으로 치환을 적용한다.
    """
    def replace_in_t(match):
        inner = match.group(1)
        # 인라인 XML 태그로 분할
        parts = re.split(r"(<[^>]+>)", inner)
        result = []
        for part in parts:
            if part.startswith("<"):
                # XML 태그는 그대로 유지
                result.append(part)
            else:
                # 텍스트 부분에만 키워드 치환 적용
                result.append(_apply_keywords_to_text(part, sorted_keywords))
        return "<hp:t>" + "".join(result) + "</hp:t>"

    return re.sub(r"<hp:t>(.*?)</hp:t>", replace_in_t, xml_text, flags=re.DOTALL)


def clone(src_path, dst_path, replacements=None, keywords=None,
          title=None, creator=None):
    """HWPX 양식을 복제하고 텍스트를 치환한다.

    Args:
        src_path: 원본 .hwpx 파일 경로
        dst_path: 출력 .hwpx 파일 경로
        replacements: Phase 1 구문 치환 dict (old → new)
        keywords: Phase 2 키워드 치환 dict (old → new), <hp:t> 내부에서만 적용
        title: 문서 제목 (메타데이터)
        creator: 작성자 (메타데이터)
    """
    replacements = replacements or {}
    sorted_keywords = _prepare_keywords(keywords) if keywords else []

    tmp_path = dst_path + ".tmp"

    with zipfile.ZipFile(src_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                if item.filename.startswith("Contents/") and item.filename.endswith(".xml"):
                    text = data.decode("utf-8")

                    # Phase 1: 구문 수준 치환 (전체 XML)
                    for old, new in replacements.items():
                        text = text.replace(old, new)

                    # Phase 2: 키워드 수준 치환 (<hp:t> 내부만)
                    if sorted_keywords:
                        text = _apply_keywords_in_xml(text, sorted_keywords)

                    # 메타데이터 치환 (content.hpf의 제목/작성자)
                    if item.filename == "Contents/content.hpf":
                        if title:
                            text = re.sub(
                                r"(<dc:title>).*?(</dc:title>)",
                                rf"\1{title}\2",
                                text,
                            )
                        if creator:
                            text = re.sub(
                                r"(<dc:creator>).*?(</dc:creator>)",
                                rf"\1{creator}\2",
                                text,
                            )

                    data = text.encode("utf-8")

                # mimetype은 반드시 ZIP_STORED
                if item.filename == "mimetype":
                    zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(item, data)

    os.replace(tmp_path, dst_path)


def validate_result(src_path, dst_path, replacements=None, keywords=None):
    """치환 결과를 검증하고 남은 원본 키워드를 보고한다.

    Returns:
        dict: {total_originals, replaced, remaining, remaining_texts, coverage_pct}
    """
    # 원본 텍스트 추출
    orig_texts = extract_texts(src_path)
    # 결과 텍스트 추출
    result_texts = extract_texts(dst_path)

    all_old_terms = set()
    if replacements:
        all_old_terms.update(replacements.keys())
    if keywords:
        all_old_terms.update(keywords.keys())

    # 결과에서 원본 키워드가 남아있는지 확인
    remaining = []
    result_full = " ".join(result_texts)
    for term in sorted(all_old_terms, key=len, reverse=True):
        if term in result_full:
            remaining.append(term)

    total = len(orig_texts)
    replaced = total - len(remaining)
    coverage = (1 - len(remaining) / max(total, 1)) * 100

    print(f"\n=== 치환 검증 ===")
    print(f"원본 텍스트 조각: {total}개")
    print(f"치환 완료: {replaced}개")
    print(f"미치환 키워드: {len(remaining)}개")
    print(f"커버리지: {coverage:.1f}%")

    if remaining:
        print(f"\n미치환 키워드:")
        for r in remaining[:20]:
            display = r[:60] + "..." if len(r) > 60 else r
            print(f"  - {display}")
        if len(remaining) > 20:
            print(f"  ... 외 {len(remaining) - 20}개")

    return {
        "total_originals": total,
        "replaced": replaced,
        "remaining": len(remaining),
        "remaining_texts": remaining,
        "coverage_pct": coverage,
    }


def main():
    parser = argparse.ArgumentParser(
        description="HWPX 양식 복제 도구 (Workflow F)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 양식 분석
  python clone_form.py --analyze sample.hwpx

  # JSON 맵으로 복제
  python clone_form.py sample.hwpx output.hwpx --map replacements.json

  # 키워드 폴백 추가
  python clone_form.py sample.hwpx output.hwpx --map map.json --keywords kw.json

  # CLI 직접 치환
  python clone_form.py sample.hwpx output.hwpx --replace "원본=대체" "A=B"
""",
    )
    parser.add_argument("source", help="원본 HWPX 파일")
    parser.add_argument("output", nargs="?", help="출력 HWPX 파일")
    parser.add_argument("--analyze", action="store_true", help="양식 분석 모드")
    parser.add_argument("--auto-analyze", metavar="JSON", help="자동 분석 + 치환 맵 템플릿 JSON 출력")
    parser.add_argument("--map", help="구문 치환 JSON 파일 (Phase 1)")
    parser.add_argument("--keywords", help="키워드 치환 JSON 파일 (Phase 2)")
    parser.add_argument("--replace", nargs="*", help="CLI 치환 쌍 (old=new)")
    parser.add_argument("--title", help="문서 제목 메타데이터")
    parser.add_argument("--creator", help="작성자 메타데이터")
    parser.add_argument("--validate", action="store_true", help="치환 후 검증 실행")

    args = parser.parse_args()

    if not os.path.exists(args.source):
        print(f"Error: 파일을 찾을 수 없음: {args.source}")
        sys.exit(1)

    # 분석 모드
    if args.analyze:
        analyze(args.source)
        return

    # 자동 분석 모드
    if args.auto_analyze:
        auto_analyze(args.source, args.auto_analyze)
        return

    # 복제 모드
    if not args.output:
        print("Error: 출력 파일을 지정하세요.")
        sys.exit(1)

    # 치환 맵 구성
    replacements = {}
    if args.map:
        with open(args.map, "r", encoding="utf-8") as f:
            replacements = json.load(f)
        print(f"구문 치환 맵: {len(replacements)}개 항목 ({args.map})")

    if args.replace:
        for pair in args.replace:
            if "=" not in pair:
                print(f"Warning: 잘못된 치환 쌍 무시: {pair}")
                continue
            old, new = pair.split("=", 1)
            replacements[old] = new
        print(f"CLI 치환: {len(args.replace)}개 추가")

    keywords = None
    if args.keywords:
        with open(args.keywords, "r", encoding="utf-8") as f:
            keywords = json.load(f)
        print(f"키워드 폴백 맵: {len(keywords)}개 항목 ({args.keywords})")

    # 복제 실행
    clone(args.source, args.output, replacements, keywords,
          title=args.title, creator=args.creator)
    print(f"복제 완료: {args.output}")

    # 검증
    if args.validate:
        validate_result(args.source, args.output, replacements, keywords)


if __name__ == "__main__":
    main()
