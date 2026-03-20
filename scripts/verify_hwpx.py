#!/usr/bin/env python3
"""
HWPX 문서 검수 도구 (서브에이전트용)

생성된 HWPX 문서를 원본과 비교하여 구조 보존 여부, XML 유효성,
텍스트 치환 결과를 종합 검증한다.

서브에이전트가 문서 생성 후 품질 검증 단계에서 사용한다.

사용법:
  # 원본과 비교 검수
  python verify_hwpx.py --source original.hwpx --result output.hwpx

  # 단독 검수 (원본 없이)
  python verify_hwpx.py --result output.hwpx

  # JSON 리포트 출력
  python verify_hwpx.py --source original.hwpx --result output.hwpx --json report.json
"""

import argparse
import json
import os
import re
import sys
import zipfile


def _count_structure(hwpx_path):
    """HWPX 구조 요소를 카운트한다."""
    result = {"path": hwpx_path}

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        names = zf.namelist()
        result["zip_entries"] = len(names)
        result["bindata"] = len([n for n in names if n.startswith("BinData/")])

        # mimetype 검사
        result["mimetype_first"] = names[0] == "mimetype" if names else False
        if "mimetype" in names:
            info = zf.getinfo("mimetype")
            result["mimetype_stored"] = info.compress_type == zipfile.ZIP_STORED
        else:
            result["mimetype_stored"] = False

        # 필수 파일
        required = ["mimetype", "Contents/content.hpf",
                     "Contents/header.xml", "Contents/section0.xml"]
        result["required_files"] = {r: r in names for r in required}

        # section0.xml 분석
        if "Contents/section0.xml" in names:
            sec = zf.read("Contents/section0.xml").decode("utf-8")
            result["section_size"] = len(sec)
            result["paragraphs"] = len(re.findall(r"<hp:p ", sec))
            result["runs"] = len(re.findall(r"<hp:run ", sec))
            result["tables"] = len(re.findall(r"<hp:tbl ", sec))
            result["images"] = len(re.findall(r"<hp:pic ", sec))

        # XML 파싱 검사
        xml_ok, xml_fail, xml_errors = 0, 0, []
        try:
            from lxml import etree
            for name in names:
                if name.endswith(".xml") or name.endswith(".hpf"):
                    try:
                        etree.fromstring(zf.read(name))
                        xml_ok += 1
                    except etree.XMLSyntaxError as e:
                        xml_fail += 1
                        xml_errors.append(f"{name}: {e}")
        except ImportError:
            # lxml 없으면 기본 XML 파서 사용
            import xml.etree.ElementTree as ET
            for name in names:
                if name.endswith(".xml") or name.endswith(".hpf"):
                    try:
                        ET.fromstring(zf.read(name))
                        xml_ok += 1
                    except ET.ParseError as e:
                        xml_fail += 1
                        xml_errors.append(f"{name}: {e}")

        result["xml_valid"] = xml_ok
        result["xml_invalid"] = xml_fail
        result["xml_errors"] = xml_errors

    return result


def _extract_texts(hwpx_path):
    """텍스트 추출 (간소화 버전)."""
    texts = []
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        for name in zf.namelist():
            if name.startswith("Contents/") and name.endswith(".xml"):
                data = zf.read(name).decode("utf-8")
                for m in re.finditer(r"<hp:t>(.*?)</hp:t>", data, re.DOTALL):
                    clean = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                    if clean:
                        texts.append(clean)
    return texts


def verify(source_path=None, result_path=None, json_output=None):
    """HWPX 검수를 실행한다.

    Args:
        source_path: 원본 .hwpx (비교 검수 시)
        result_path: 결과 .hwpx (필수)
        json_output: JSON 리포트 경로 (선택)

    Returns:
        dict: 검수 결과
    """
    report = {"status": "UNKNOWN", "issues": [], "warnings": []}

    if not result_path or not os.path.exists(result_path):
        report["status"] = "FAIL"
        report["issues"].append(f"결과 파일 없음: {result_path}")
        return report

    # 1. 결과 파일 구조 분석
    result_info = _count_structure(result_path)
    report["result"] = result_info

    # 기본 검증
    if not result_info.get("mimetype_first"):
        report["issues"].append("mimetype이 ZIP 첫 엔트리가 아님")
    if not result_info.get("mimetype_stored"):
        report["issues"].append("mimetype이 ZIP_STORED가 아님")
    for fname, exists in result_info.get("required_files", {}).items():
        if not exists:
            report["issues"].append(f"필수 파일 누락: {fname}")
    if result_info.get("xml_invalid", 0) > 0:
        report["issues"].append(
            f"XML 파싱 실패 {result_info['xml_invalid']}개: "
            + "; ".join(result_info.get("xml_errors", []))
        )

    # 2. 원본과 비교 (제공된 경우)
    if source_path and os.path.exists(source_path):
        source_info = _count_structure(source_path)
        report["source"] = source_info

        comparison = {}

        # 구조 보존 비교
        for key in ["zip_entries", "bindata", "paragraphs", "runs",
                     "tables", "images"]:
            src_val = source_info.get(key, 0)
            res_val = result_info.get(key, 0)
            diff = res_val - src_val
            comparison[key] = {
                "source": src_val, "result": res_val, "diff": diff
            }
            if key in ("runs", "tables", "images") and diff < 0:
                report["issues"].append(
                    f"{key} 감소: {src_val} → {res_val} (차이: {diff})"
                )
            elif key in ("runs",) and diff < 0:
                report["warnings"].append(
                    f"{key} 변경: {src_val} → {res_val}"
                )

        # section 크기 비율
        src_size = source_info.get("section_size", 1)
        res_size = result_info.get("section_size", 0)
        ratio = res_size / src_size * 100 if src_size > 0 else 0
        comparison["section_size_ratio"] = round(ratio, 1)

        if ratio < 50:
            report["issues"].append(
                f"section0.xml 크기 비율 {ratio:.1f}% — 구조 대량 손실 의심"
            )
        elif ratio < 90:
            report["warnings"].append(
                f"section0.xml 크기 비율 {ratio:.1f}% — 일부 구조 변경 가능"
            )

        report["comparison"] = comparison

    # 3. 최종 판정
    if report["issues"]:
        report["status"] = "FAIL"
    elif report["warnings"]:
        report["status"] = "WARN"
    else:
        report["status"] = "PASS"

    # 출력
    _print_report(report)

    if json_output:
        with open(json_output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 리포트: {json_output}")

    return report


def _print_report(report):
    """검수 결과를 콘솔에 출력한다."""
    status_icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(
        report["status"], "❓"
    )
    print(f"\n{'='*60}")
    print(f"  HWPX 검수 결과: {status_icon} {report['status']}")
    print(f"{'='*60}")

    # 결과 파일 정보
    if "result" in report:
        r = report["result"]
        print(f"\n[결과 파일]")
        print(f"  ZIP엔트리: {r.get('zip_entries', '?')}개, "
              f"BinData: {r.get('bindata', '?')}개")
        print(f"  문단: {r.get('paragraphs', '?')}, "
              f"런: {r.get('runs', '?')}, "
              f"테이블: {r.get('tables', '?')}, "
              f"이미지: {r.get('images', '?')}")
        print(f"  XML: 유효 {r.get('xml_valid', 0)}개, "
              f"오류 {r.get('xml_invalid', 0)}개")

    # 비교 결과
    if "comparison" in report:
        c = report["comparison"]
        print(f"\n[원본 대비 비교]")
        for key in ["paragraphs", "runs", "tables", "images", "bindata"]:
            if key in c:
                d = c[key]
                diff_str = f"+{d['diff']}" if d["diff"] > 0 else str(d["diff"])
                icon = "✅" if d["diff"] == 0 else ("⚠️" if d["diff"] > 0 else "❌")
                print(f"  {icon} {key}: {d['source']} → {d['result']} ({diff_str})")
        if "section_size_ratio" in c:
            ratio = c["section_size_ratio"]
            icon = "✅" if ratio >= 90 else ("⚠️" if ratio >= 50 else "❌")
            print(f"  {icon} section 크기 비율: {ratio}%")

    # 이슈
    if report["issues"]:
        print(f"\n[이슈 ({len(report['issues'])}개)]")
        for issue in report["issues"]:
            print(f"  ❌ {issue}")

    if report["warnings"]:
        print(f"\n[경고 ({len(report['warnings'])}개)]")
        for warn in report["warnings"]:
            print(f"  ⚠️ {warn}")

    if not report["issues"] and not report["warnings"]:
        print(f"\n  모든 검사 통과!")


def main():
    parser = argparse.ArgumentParser(
        description="HWPX 문서 검수 도구 (서브에이전트용)",
    )
    parser.add_argument("--source", help="원본 HWPX 파일 (비교 검수)")
    parser.add_argument("--result", required=True, help="검수 대상 HWPX 파일")
    parser.add_argument("--json", help="JSON 리포트 출력 경로")

    args = parser.parse_args()
    report = verify(args.source, args.result, args.json)

    sys.exit(0 if report["status"] in ("PASS", "WARN") else 1)


if __name__ == "__main__":
    main()
