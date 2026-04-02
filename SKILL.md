---
name: hwpx
description: "HWP/HWPX 문서(.hwp, .hwpx) 생성·변환·읽기·편집 통합 스킬. 'HWP 변환', 'hwp를 hwpx로', '한글 문서', 'hwpx', 'HWPX', '한글파일', '.hwpx 만들어줘', '보고서', '공문', '기안문', '한글로 작성', '회의록', '제안서', '이미지 포함 문서' 등의 키워드 시 반드시 사용. HWP→HWPX 변환, 마크다운·텍스트·URL→HWPX 변환, 템플릿 치환 워크플로우를 지원한다."
allowed-tools: Bash(python3 *), Read, Write, Glob, Grep
---

# HWPX 통합 문서 스킬

HWPX는 한컴오피스 한글의 개방형 문서 포맷이다. **ZIP 패키지 + XML 파트** 구조.

## 스킬 디렉토리

```
${CLAUDE_SKILL_DIR}/
├── SKILL.md
├── scripts/
│   ├── hwpx_helpers.py        # ★ 헬퍼 라이브러리 (배너/섹션바/이미지/빌드 함수)
│   ├── convert_hwp.py         # ★ HWP→HWPX 변환 (Workflow H)
│   ├── build_hwpx.py          # 템플릿+XML → .hwpx 조립
│   ├── fix_namespaces.py      # ★ 필수: 네임스페이스 후처리
│   ├── validate.py            # HWPX 구조 검증
│   ├── analyze_template.py    # HWPX 심층 분석
│   ├── clone_form.py           # ★ 양식 복제 (Workflow F)
│   ├── verify_hwpx.py         # ★ 서브에이전트 검수 도구
│   ├── text_extract.py        # 텍스트 추출
│   ├── md2hwpx.py             # 마크다운→HWPX 자동 변환
│   └── office/{unpack,pack}.py
├── templates/
│   ├── base/                  # 베이스 Skeleton
│   ├── report/                # 보고서
│   ├── gonmun/                # 공문
│   ├── minutes/               # 회의록
│   ├── proposal/              # 제안서
│   └── government/            # ★ 관공서 (컬러 섹션 바/표지 배너)
├── assets/
│   ├── report-template.hwpx
│   └── government-reference.hwpx
└── references/
    ├── xml-structure.md       # XML 구조, 이미지 삽입, 표지/섹션 바 패턴
    ├── template-styles.md     # 템플릿별 스타일 ID 맵
    ├── troubleshooting.md     # 트러블슈팅
    ├── report-style.md        # 보고서 양식 상세
    ├── official-doc-style.md  # 공문서 양식 상세
    ├── gonmunseo-2025-writing-rules.md  # ★ 2025 개정 공문서 작성법
    └── xml-internals.md       # 저수준 XML 구조
```

## 환경 설정

```bash
pip install python-hwpx lxml --break-system-packages
# HWP→HWPX 변환 (Workflow H) 추가 의존성:
pip install pyhwp5 olefile --break-system-packages
```

---

## ★ 워크플로우 선택 (Decision Tree)

> **반드시 아래 판단을 따른다.**

```
사용자 요청
 ├─ ".hwp 파일 → .hwpx 변환" → 워크플로우 H (HWP→HWPX 변환) ★★
 ├─ "마크다운/텍스트/URL → HWPX" → 워크플로우 A (콘텐츠→HWPX)
 ├─ "양식에 내용 채워줘" → 워크플로우 B (템플릿 치환)
 ├─ "HWPX 수정해줘" → 워크플로우 C (기존 문서 편집)
 ├─ "이 HWPX 양식으로 만들어줘" → 워크플로우 D (레퍼런스 기반)
 ├─ "이 양식 복제해서 내용 바꿔줘" → 워크플로우 F (양식 복제) ★
 ├─ "공문 작성해줘/공문서 검수해줘" → 워크플로우 G (공문서 작성법 준수) ★
 └─ "HWPX 읽어줘" → 워크플로우 E (읽기/추출)
```

### ⚠️ 자동 판별 규칙 (사용자가 .hwp 파일을 제공한 경우)

> **사용자가 `.hwp` 파일을 주면 먼저 워크플로우 H로 HWPX 변환 후 후속 워크플로우를 진행한다.**

```
입력 파일 확인
 ├─ .hwp 파일 → 워크플로우 H로 HWPX 변환
 │   ├─ "변환만 해줘" → 변환 후 종료
 │   ├─ "내용 바꿔줘" → 변환 후 워크플로우 F
 │   ├─ "읽어줘/텍스트 추출" → 변환 후 워크플로우 E
 │   └─ "수정해줘" → 변환 후 워크플로우 C
 └─ .hwpx 파일 → 기존 워크플로우 판별 (아래)
```

### ⚠️ 자동 판별 규칙 (사용자가 양식 파일을 제공한 경우)

> **사용자가 `.hwpx` 파일을 주고 "이걸로 테스트", "내용 바꿔줘", "이 양식으로" 등을 요청하면
> 먼저 `clone_form.py --analyze`로 구조를 확인한다.**

```
양식 분석 결과
 ├─ 테이블 ≥ 1개 또는 이미지 ≥ 1개 → 워크플로우 F (양식 복제) ★★★
 ├─ 테이블 0개, 이미지 0개, 단순 텍스트 → 워크플로우 C 또는 D 가능
 └─ 판단 불가 → 워크플로우 F를 기본으로 사용 (가장 안전)
```

> **절대 하지 말 것:**
> - `<hp:t>` 노드를 순차적으로 새 텍스트로 덮어쓰기 — **런(run) 소실, 서식 파괴**
> - lxml로 텍스트 노드를 직접 조작 — **네임스페이스/속성 손실 위험**
> - 새 section0.xml을 처음부터 작성 (Workflow A/D) — **구조 97.5% 손실**
>
> **반드시 할 것:**
> - `clone_form.py`의 `clone()` 함수 또는 ZIP-level 문자열 치환 사용
> - 치환은 `str.replace()` 기반으로 XML 구조를 건드리지 않음

---

## 워크플로우 A: 콘텐츠 → HWPX (가장 중요!)

> **마크다운·텍스트·URL → 구조화된 HWPX 문서. 이 워크플로우가 핵심.**

> **⚠️ md2hwpx.py를 직접 실행하지 마라.** md2hwpx.py는 base/report 템플릿만 지원하며,
> government 템플릿의 컬러 배너·섹션 바·표지 페이지를 생성할 수 없다.
> **반드시 `hwpx_helpers.py`를 import하고 아래 흐름을 따른다.**

### 전체 흐름

```
[1] 소스 자료 읽기
[2] 구조 파싱 (제목, 섹션, 본문, 이미지)
[3] 템플릿 선택 → 해당 템플릿의 스타일 ID만 사용 (references/template-styles.md)
    ⚠️ 템플릿 간 ID는 호환되지 않음! government charPr를 report에 쓰면 깨짐
[4] hwpx_helpers.py를 import하여 Python 빌드 스크립트 작성
[5] build_hwpx.py로 .hwpx 조립
[6] 이미지가 있으면 add_images_to_hwpx() + update_content_hpf()
[7] fix_namespaces.py 후처리 (필수!)
[8] validate.py 검증
```

> **올바른 방식**: `from hwpx_helpers import *` → `make_cover_page()` → `make_section_bar()` → `make_body_para()`
> **잘못된 방식**: `python3 md2hwpx.py input.md` (컬러 배너·섹션 바 없음, 기본 스타일만 적용)

### section0.xml 핵심 규칙

1. **첫 문단 첫 run에 secPr + colPr 필수** — 없으면 문서가 안 열림
2. **모든 문단 id는 고유 정수**
3. **XML 특수문자 `<>&"` 반드시 이스케이프**
4. **표지→본문 사이 `pageBreak="1"` 문단 삽입**

> XML 구조 상세: [references/xml-structure.md](references/xml-structure.md)

### 빌드 명령

```bash
# 1. section0.xml을 임시 파일로 작성 (Python 스크립트로 생성)

# 2. 빌드 (government 템플릿 사용 시)
python3 "${CLAUDE_SKILL_DIR}/scripts/build_hwpx.py" \
  --header "${CLAUDE_SKILL_DIR}/templates/government/header.xml" \
  --section /tmp/section0.xml \
  --title "문서 제목" \
  --output result.hwpx

# 3. 네임스페이스 후처리 (필수!)
python3 "${CLAUDE_SKILL_DIR}/scripts/fix_namespaces.py" result.hwpx

# 4. 검증
python3 "${CLAUDE_SKILL_DIR}/scripts/validate.py" result.hwpx
```

### Python 빌드 스크립트 패턴

> **`scripts/hwpx_helpers.py`를 import하여 검증된 함수를 재사용한다.**

```python
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path("${CLAUDE_SKILL_DIR}/scripts")))
from hwpx_helpers import *

SKILL_DIR = Path("${CLAUDE_SKILL_DIR}")
REF_HWPX = SKILL_DIR / "assets" / "government-reference.hwpx"
OUTPUT = Path("output.hwpx")

# 0. government header 검증 (잘못된 header 사용 방지)
GOV_HEADER = SKILL_DIR / "templates/government/header.xml"
validate_header_for_government(GOV_HEADER)

# 1. secPr 추출
secpr, colpr = extract_secpr_and_colpr(REF_HWPX)

# 2. section0.xml 조립
parts = []
parts.append(f'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>')
parts.append(f'<hs:sec {NS_DECL}>')
parts.append(make_first_para(secpr, colpr))
parts.extend(make_cover_page("문서 제목", subtitle="(부제)", date="2026. 3."))
parts.append(make_cover_banner("문서 제목"))  # 본문 페이지 배너
parts.append(make_empty_line())
parts.append(make_section_bar("1", "섹션 제목"))
parts.append(make_body_para("가.", "본문 내용"))
parts.append(f'</hs:sec>')
section_xml = "\n".join(parts)

# 3. 빌드
Path("/tmp/section0.xml").write_text(section_xml, encoding="utf-8")
subprocess.run(["python3", str(SKILL_DIR/"scripts/build_hwpx.py"),
    "--header", str(SKILL_DIR/"templates/government/header.xml"),
    "--section", "/tmp/section0.xml", "--output", str(OUTPUT)], check=True)

# 4. (이미지 있으면) add_images_to_hwpx() + update_content_hpf()

# 5. 후처리 + 검증
subprocess.run(["python3", str(SKILL_DIR/"scripts/fix_namespaces.py"), str(OUTPUT)], check=True)
subprocess.run(["python3", str(SKILL_DIR/"scripts/validate.py"), str(OUTPUT)])
```

### hwpx_helpers.py 제공 함수

| 함수 | 설명 |
|------|------|
| `next_id()` | 고유 ID 생성 |
| `xml_escape(text)` | XML 특수문자 이스케이프 |
| `validate_header_for_government(path)` | header.xml이 government용인지 검증 (크기·charPr 수 체크) |
| `extract_secpr_and_colpr(hwpx)` | HWPX에서 secPr+colPr 추출 |
| `make_first_para(secpr, colpr)` | 첫 문단 (secPr 포함) |
| `make_empty_line()` | 빈 줄 |
| `make_page_break()` | 페이지 넘김 |
| `make_text_para(text, charpr, parapr)` | 텍스트 문단 |
| `make_body_para(marker, text)` | 본문 (마커+내용) |
| `make_cover_banner(title)` | 표지 배너 (3×2 컬러 테이블) |
| `make_section_bar(number, title)` | 섹션 바 (1×3 컬러 테이블) |
| `make_cover_page(title, subtitle, date)` | 표지 전체 + pageBreak |
| `make_image_para(binary_item_id, w, h)` | 이미지 (전체 hp:pic 구조) |
| `add_images_to_hwpx(path, images)` | ZIP에 이미지 추가 |
| `update_content_hpf(path, images)` | content.hpf에 이미지 등록 |
| `NS_DECL` | 네임스페이스 선언 상수 |

> 스타일 ID 상세: [references/template-styles.md](references/template-styles.md)

### 이미지 포함 시

> **이미지 `<hp:pic>` 구조가 불완전하면 한컴오피스가 크래시한다.**
> 반드시 [references/xml-structure.md](references/xml-structure.md)의 "이미지 삽입" 섹션을 읽고 전체 구조를 사용할 것.

---

## 워크플로우 B: 템플릿 치환

> **기존 양식의 플레이스홀더를 교체. 양식 문서에 적합.**

```
[1] 양식 파일 복사 → [2] ObjectFinder로 텍스트 조사
[3] 플레이스홀더 매핑 → [4] ZIP-level 치환 → [5] fix_namespaces.py → [6] 검증
```

### ZIP-level 치환

```python
import zipfile, os

def zip_replace(src, dst, replacements):
    tmp = dst + ".tmp"
    with zipfile.ZipFile(src, "r") as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("Contents/") and item.filename.endswith(".xml"):
                    text = data.decode("utf-8")
                    for old, new in replacements.items():
                        text = text.replace(old, new)
                    data = text.encode("utf-8")
                if item.filename == "mimetype":
                    zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(item, data)
    os.replace(tmp, dst)
```

### 양식 선택 정책

1. 사용자 업로드 양식 → 해당 파일 사용
2. `${CLAUDE_SKILL_DIR}/assets/report-template.hwpx`
3. HwpxDocument.new()는 최후의 수단

---

## 워크플로우 C: 기존 문서 편집

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/office/unpack.py" doc.hwpx ./unpacked/
# XML 편집 후
python3 "${CLAUDE_SKILL_DIR}/scripts/office/pack.py" ./unpacked/ edited.hwpx
python3 "${CLAUDE_SKILL_DIR}/scripts/fix_namespaces.py" edited.hwpx
```

## 워크플로우 D: 레퍼런스 기반 생성

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/analyze_template.py" reference.hwpx
# header.xml 추출 후 동일 스타일 ID로 새 section0.xml 작성
python3 "${CLAUDE_SKILL_DIR}/scripts/build_hwpx.py" \
  --header /tmp/ref_header.xml --section /tmp/new_section.xml --output result.hwpx
python3 "${CLAUDE_SKILL_DIR}/scripts/fix_namespaces.py" result.hwpx
```

## 워크플로우 E: 읽기/추출

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/text_extract.py" doc.hwpx
python3 "${CLAUDE_SKILL_DIR}/scripts/text_extract.py" doc.hwpx --format markdown
```

---

## 워크플로우 F: 양식 복제 (★ 복잡한 양식에 필수)

> **기존 HWPX를 통째로 복사 + 텍스트만 치환. 테이블·이미지·스타일 100% 보존.**
>
> ⚠️ **테이블 5개 이상 또는 이미지 포함이면 반드시 워크플로우 F 사용.**
> 워크플로우 D는 header만 재활용하고 section을 새로 만들기 때문에 구조의 97.5%를 잃는다.

### 전체 흐름

```
[1] 원본 양식 분석:  clone_form.py --analyze sample.hwpx
[2] 구문 치환 맵 작성 (JSON): {"원본 문구": "새 문구", ...}
[3] (선택) 키워드 폴백 맵 작성: {"재난": "교육위기", "안전": "AI교육", ...}
[4] 복제 실행:  clone_form.py sample.hwpx output.hwpx --map map.json --keywords kw.json
[5] fix_namespaces.py 후처리 (필수!)
[6] validate.py 검증
```

### 2단계 치환 전략

| 단계 | 범위 | 용도 |
|------|------|------|
| Phase 1 (--map) | 전체 XML | 긴 문구·문장 단위 치환 |
| Phase 2 (--keywords) | `<hp:t>` 내부만 | 남은 키워드 개별 치환 (폴백) |

> 키워드는 길이 내림차순 정렬하여 "재난안전관리"가 "재난"보다 먼저 매칭된다.
> Phase 2는 `<hp:t>` 태그 안의 텍스트만 대상이므로 XML 구조를 손상시키지 않는다.

### CLI 사용법

```bash
# 분석
python3 "${CLAUDE_SKILL_DIR}/scripts/clone_form.py" --analyze sample.hwpx

# 복제 (구문 치환만)
python3 "${CLAUDE_SKILL_DIR}/scripts/clone_form.py" \
  sample.hwpx output.hwpx --map replacements.json

# 복제 (구문 + 키워드 폴백)
python3 "${CLAUDE_SKILL_DIR}/scripts/clone_form.py" \
  sample.hwpx output.hwpx --map map.json --keywords keywords.json --validate

# 후처리 (필수!)
python3 "${CLAUDE_SKILL_DIR}/scripts/fix_namespaces.py" output.hwpx
python3 "${CLAUDE_SKILL_DIR}/scripts/validate.py" output.hwpx
```

### Python API

```python
from clone_form import clone, analyze, extract_texts, validate_result

# 분석
texts = analyze("sample.hwpx")

# 복제
clone("sample.hwpx", "output.hwpx",
      replacements={"원본 문구": "새 문구"},
      keywords={"재난": "교육위기"},
      title="새 문서 제목", creator="작성자")

# 검증
result = validate_result("sample.hwpx", "output.hwpx",
                         replacements={...}, keywords={...})
print(f"커버리지: {result['coverage_pct']:.1f}%")
```

### 워크플로우 D vs F 비교

| 항목 | D (레퍼런스 기반) | F (양식 복제) |
|------|------------------|--------------|
| 원본 구조 보존 | ~2.5% | **100%** |
| 테이블 | ❌ 재구성 필요 | ✅ 그대로 |
| 이미지 | ❌ BinData 누락 | ✅ 그대로 |
| 스타일 | ⚠️ ID 매칭 필요 | ✅ 그대로 |
| 적합한 경우 | 간단한 텍스트 문서 | **복잡한 양식** |

---

## 서브에이전트 검수 (★ 권장)

> **문서 생성 후 별도 서브에이전트를 생성하여 품질 검증을 수행한다.**
> 생성 에이전트와 검수 에이전트를 분리하면 실수를 줄일 수 있다.

### 검수 도구

```bash
# 원본과 비교 검수 (구조 보존 확인)
python3 "${CLAUDE_SKILL_DIR}/scripts/verify_hwpx.py" \
  --source original.hwpx --result output.hwpx

# 단독 검수 (XML 유효성 + 구조 체크)
python3 "${CLAUDE_SKILL_DIR}/scripts/verify_hwpx.py" --result output.hwpx

# JSON 리포트 출력 (자동화용)
python3 "${CLAUDE_SKILL_DIR}/scripts/verify_hwpx.py" \
  --source original.hwpx --result output.hwpx --json report.json
```

### 검수 항목

| 검사 | 내용 | FAIL 조건 |
|------|------|-----------|
| mimetype | 첫 엔트리 + ZIP_STORED | 위치·압축 불일치 |
| 필수 파일 | header.xml, section0.xml 등 | 누락 시 |
| XML 유효성 | 모든 XML 파싱 가능 | 파싱 오류 |
| 런 보존 | 원본 대비 런(run) 수 | **감소 시 FAIL** |
| 테이블·이미지 | 원본 대비 수량 | 감소 시 FAIL |
| section 크기 | 원본 대비 비율 | 50% 미만 시 FAIL |

### 서브에이전트 워크플로우 예시

```
[메인 에이전트]
  1. clone_form.py로 문서 생성
  2. fix_namespaces.py 후처리
  ↓
[검수 서브에이전트 생성]
  3. verify_hwpx.py --source --result 실행
  4. text_extract.py로 텍스트 추출 확인
  5. PASS/FAIL 리포트 반환
  ↓
[메인 에이전트]
  6. FAIL이면 수정 후 재검수
  7. PASS이면 사용자에게 전달
```

---

## 워크플로우 G: 공문서 작성법 준수 (2025 개정) ★

> **공문서(기안문) 본문 작성 시 2025 개정 공문서 작성법을 자동 적용.**
> 공문서 HWPX 생성(Workflow A/B/F)과 결합하여 사용하거나, 기존 공문서 텍스트 검수에 단독 사용.

### 트리거 조건

- "공문 작성해줘", "공문서 만들어줘", "기안문 작성", "공문 검수" 등
- Workflow A/B/F에서 공문서 유형 감지 시 자동 결합

### 전체 흐름

```
[1] 사용자 요청 분석 (작성 vs 검수)
[2] references/gonmunseo-2025-writing-rules.md 참조
[3-A] 작성 모드: 공문서 작성법 규칙에 따라 본문 생성
[3-B] 검수 모드: 기존 텍스트를 규칙 대비 검수 → 수정 제안
[4] HWPX 생성 시 Workflow A 또는 gonmun 템플릿 사용
[5] fix_namespaces.py + validate.py
```

### 작성 모드: 공문서 본문 자동 생성

사용자가 주제·목적·내용을 제공하면, 아래 규칙을 **모두** 적용하여 본문을 생성한다.

#### 필수 적용 규칙 체크리스트

| # | 규칙 | 적용 |
|---|------|------|
| 1 | 1안건 1기안 원칙 | 제목이 내용을 모두 포괄하는지 확인 |
| 2 | 항목 기호 8단계 | 1. → 가. → 1) → 가) → ⑴ → ㈎ → ① → ㉮ |
| 3 | 들여쓰기 2타 규칙 | 하위 항목마다 2타씩 오른쪽 |
| 4 | 날짜 표기 | `2026. 3. 23.` (0 없음, 마침표 필수) |
| 5 | 시간 표기 | 24시각제 `09:00`, `15:30` |
| 6 | 금액 표기 | `금500,000원(금오십만원)` |
| 7 | 한글 원칙 | 외국어·한자는 괄호 안 |
| 8 | 끝 표시 | 마지막에서 1자 띄우고 "끝" |
| 9 | 붙임 표시 | 쌍점 없음, 1자 여백, 개별 표기 |
| 10 | 관련 근거 | 문서번호+날짜+문서명 포함 |
| 11 | 수신자 표기 | 기관장(업무처리 보조기관) 형식 |
| 12 | 종결어미 | 평서형 '-다' 또는 '-ㅂ니다' |
| 13 | 낫표 | 법령은 「 」, 책·신문은 『 』 |
| 14 | 높임법 | '-시-' 사용, '-오-' 미사용 |
| 15 | 등(들) | 생략 용도로만 사용 |

#### 생성 예시

```python
# 사용자: "K-에듀파인시스템 담당자 협의 안내 공문 만들어줘"

body_lines = [
    "1. 관련: 교육정책과-1234(2026. 2. 1.)",
    "2. K-에듀파인시스템을 활용한 학교업무 개선 및 효율화 방안 마련을 위하여 "
    "아래와 같이 담당자 협의를 안내하오니 대상자가 참석할 수 있도록 "
    "협조하여 주시기 바랍니다.",
    "  가. 일시: 2026. 3. 25.(수) 15:00∼17:00",
    "  나. 장소: 경기도교육청 소회의실8(남부청사 4층)",
    "  다. 대상: K-에듀파인시스템 운영분과 위원 및 업무 담당자 20명",
    "  라. 내용: K-에듀파인시스템을 활용한 학교업무 개선 및 효율화 정책 방향 모색",
    "  마. 협조 사항",
    "    1) 원활한 회의 진행을 위해 14:50까지 참석자 등록 완료",
    "    2) 청사 내 주차 공간이 협소하므로 대중교통 이용 권장",
    "",
    "붙임  K-에듀파인시스템 운영분과 위원 명단 1부.  끝.",
]
```

### 검수 모드: 기존 공문서 텍스트 검수

```
[1] text_extract.py로 텍스트 추출
[2] 아래 검수 항목별 위반 여부 확인
[3] 위반 사항 목록 + 수정 제안 출력
```

#### 검수 항목

| 검수 항목 | 확인 내용 | 위반 예시 |
|----------|----------|----------|
| 날짜 형식 | `YYYY. M. D.` (0 없음, 마침표) | `2025.1.06.`, `'24. 1. 6.` |
| 시간 형식 | 24시각제, 쌍점 | `오전 9시`, `오후 3시 20분` |
| 금액 형식 | 아라비아 숫자+한글 병기 | `345천원`, 띄어쓰기 오류 |
| 항목 기호 순서 | 8단계 순서 준수 | 1단계에서 바로 3단계로 건너뜀 |
| 들여쓰기 | 2타 규칙 | 들여쓰기 불일치 |
| 끝 표시 | 1자 띄우고 "끝" | "끝" 누락, 띄움 오류 |
| 붙임 형식 | 쌍점 없음, 개별 표기 | `붙임:`, 묶어서 표기 |
| 한글 원칙 | 외국어 괄호 안 | `R&D`, `IT` 단독 사용 |
| 수신자 형식 | 기관장(보조기관) | 형식 미준수 |
| 낫표 사용 | 법령 「 」, 책 『 』 | 큰따옴표로 법명 인용 |
| 관련 근거 | 문서명 포함 | 문서명 누락 |
| 종결어미 | '-다' 또는 존칭 | 비표준 종결 |

### Workflow A/B/F와 결합 시

공문서 생성 요청이 감지되면:

1. **Workflow G 규칙으로 본문 텍스트 생성** (이 워크플로우)
2. **Workflow A**로 gonmun 템플릿 기반 HWPX 생성, 또는
3. **Workflow F**로 기존 공문 양식에 텍스트 치환

> 상세 규칙: [references/gonmunseo-2025-writing-rules.md](references/gonmunseo-2025-writing-rules.md)

---

## 워크플로우 H: HWP → HWPX 변환 ★★

> **HWP(바이너리) 파일을 HWPX(개방형 XML)로 변환. 이미지·도형·표 포함 문서 지원.**
>
> 변환 후 다른 워크플로우(E/C/F)와 조합 가능.

### 트리거 조건

- 사용자가 `.hwp` 파일 경로를 제공
- "HWP를 HWPX로 변환", "한글 파일 변환", "hwp 파일 열어줘" 등

### 전체 흐름

```
[1] .hwp 파일 확인
[2] convert_hwp.py로 변환 → .hwpx 생성
[3] validate.py 검증
[4] (선택) 후속 워크플로우 진행 (E/C/F)
```

### CLI 사용법

```bash
# 기본 변환 (같은 이름 .hwpx로 출력)
python3 "${CLAUDE_SKILL_DIR}/scripts/convert_hwp.py" input.hwp

# 출력 경로 지정
python3 "${CLAUDE_SKILL_DIR}/scripts/convert_hwp.py" input.hwp -o output.hwpx

# 문서 정보 확인 (변환 없이)
python3 "${CLAUDE_SKILL_DIR}/scripts/convert_hwp.py" input.hwp --info

# JSON 출력
python3 "${CLAUDE_SKILL_DIR}/scripts/convert_hwp.py" input.hwp --info --json
```

### Python API

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("${CLAUDE_SKILL_DIR}/scripts")))
from convert_hwp import convert, info

# 변환
output_path = convert("input.hwp", "output.hwpx")

# 정보 확인
metadata = info("input.hwp")
print(metadata["title"], metadata["section_count"])
```

### 변환 후 후속 작업 예시

```bash
# HWP → HWPX 변환
python3 "${CLAUDE_SKILL_DIR}/scripts/convert_hwp.py" doc.hwp -o doc.hwpx

# 검증
python3 "${CLAUDE_SKILL_DIR}/scripts/validate.py" doc.hwpx

# 텍스트 추출 (Workflow E)
python3 "${CLAUDE_SKILL_DIR}/scripts/text_extract.py" doc.hwpx

# 양식 복제 (Workflow F)
python3 "${CLAUDE_SKILL_DIR}/scripts/clone_form.py" doc.hwpx output.hwpx --map map.json
python3 "${CLAUDE_SKILL_DIR}/scripts/fix_namespaces.py" output.hwpx
```

### 의존성

```bash
pip install pyhwp5 olefile lxml --break-system-packages
```

> `convert_hwp.py`는 누락된 패키지를 자동으로 설치하며,
> `hwp2hwpx-python-refactor` 레포가 없으면 자동으로 클론한다.

### 지원 범위

| 항목 | 지원 |
|------|------|
| 텍스트 | ✅ |
| 표 | ✅ |
| 이미지 (PNG/JPG/BMP/GIF) | ✅ |
| 도형 (사각형/원/선) | ✅ |
| 컨테이너 (그룹 도형) | ✅ |
| 각주/미주 | ✅ |
| 다단 | ✅ |
| 머리말/꼬리말 | ✅ |
| OLE 객체 | ⚠️ 부분 지원 |
| 수식 | ❌ 미지원 |

---

## 네임스페이스 후처리 (★ 필수)

> **⚠️ 빠뜨리면 한글 Viewer에서 빈 페이지로 표시된다!**

```python
import subprocess
subprocess.run(["python3", f"{SKILL_DIR}/scripts/fix_namespaces.py", "output.hwpx"], check=True)
```

| URI | 프리픽스 |
|-----|---------|
| `.../2011/head` | `hh` |
| `.../2011/core` | `hc` |
| `.../2011/paragraph` | `hp` |
| `.../2011/section` | `hs` |

---

## 단위 변환

| 값 | HWPUNIT | 의미 |
|----|---------|------|
| 1pt | 100 | 기본 단위 |
| 1mm | 283.5 | 밀리미터 |
| A4 폭 | 59528 | 210mm |
| A4 높이 | 84186 | 297mm |
| 좌우여백 | 8504 | 30mm |
| 본문폭 | 42520 | 150mm |

---

## Critical Rules

1. **HWP+HWPX 지원**: `.hwp`(바이너리)는 워크플로우 H로 HWPX 변환 후 처리
2. **secPr 필수**: 첫 문단 첫 run에 secPr + colPr
3. **mimetype**: 첫 ZIP 엔트리, ZIP_STORED
4. **네임스페이스**: `hp:`, `hs:`, `hh:`, `hc:` 접두사 유지
5. **fix_namespaces 필수**: 모든 빌드 후 반드시 실행
6. **fix_namespaces 호출법**: `subprocess.run()` 사용 (`exec()` 금지)
7. **build_hwpx.py 우선**: 새 문서는 build_hwpx.py 사용
8. **검증 필수**: 생성 후 validate.py 실행
9. **XML 이스케이프**: `<>&"` 반드시 이스케이프
10. **ID 고유성**: 모든 문단 id는 문서 내 고유
11. **이미지**: `<hp:pic>` 필수 구조 준수 → [xml-structure.md](references/xml-structure.md)
12. **템플릿 ID 호환 불가**: government charPr/paraPr/borderFill ID를 report/base 등 다른 템플릿에 사용하면 깨짐. 반드시 해당 템플릿의 ID만 사용. base charPr 3은 "16pt 제목"이 아니라 "9pt 각주"임에 주의
13. **hwpx_helpers.py 사용 필수**: md2hwpx.py 직접 실행 금지. 반드시 `from hwpx_helpers import *`로 함수를 사용하여 빌드 스크립트를 작성할 것. md2hwpx.py는 government 템플릿(컬러 배너/섹션 바)을 지원하지 않음
14. **양식 복제 시 Workflow F 필수**: 사용자가 `.hwpx` 양식을 제공하고 내용 변경을 요청하면 `clone_form.py` 사용. 절대로 `<hp:t>` 노드를 순차 덮어쓰기하거나 lxml로 텍스트를 직접 조작하지 말 것 (런 소실·서식 파괴 원인)
15. **서브에이전트 검수 권장**: 문서 생성 후 별도 서브에이전트로 `validate.py` + `text_extract.py` + 구조 비교를 실행하여 품질 검증

---

## 상세 참조

- **XML 구조·이미지·표지 패턴**: [references/xml-structure.md](references/xml-structure.md)
- **템플릿별 스타일 ID 맵**: [references/template-styles.md](references/template-styles.md)
- **트러블슈팅**: [references/troubleshooting.md](references/troubleshooting.md)
- **보고서 양식**: [references/report-style.md](references/report-style.md)
- **공문서 양식**: [references/official-doc-style.md](references/official-doc-style.md)
- **2025 개정 공문서 작성법**: [references/gonmunseo-2025-writing-rules.md](references/gonmunseo-2025-writing-rules.md)
- **보고서 기호**: □(16pt) → ○(15pt) → ―(15pt) → ※(13pt)
- **공문서 번호**: 1. → 가. → 1) → 가) → (1) → (가) → ① → ㉮
