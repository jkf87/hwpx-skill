# hwpx-skill

HWP/HWPX 문서 생성, 변환, 읽기, 편집을 위한 Claude 스킬.

## 기능

| 워크플로우 | 설명 |
|-----------|------|
| **A** | 마크다운/텍스트/URL → HWPX 문서 생성 |
| **B** | 템플릿 플레이스홀더 치환 |
| **C** | 기존 HWPX 문서 편집 (unpack → 수정 → pack) |
| **D** | 레퍼런스 HWPX 기반 새 문서 생성 |
| **E** | HWPX 텍스트 읽기/추출 |
| **F** | 양식 복제 (테이블/이미지/스타일 100% 보존) |
| **G** | 2025 개정 공문서 작성법 준수 |
| **H** | **HWP(바이너리) → HWPX 변환** |

## 설치

```bash
# 기본 의존성
pip install python-hwpx lxml --break-system-packages

# HWP→HWPX 변환 (워크플로우 H) 추가 의존성
pip install pyhwp5 olefile --break-system-packages
```

## 빠른 시작

### HWP → HWPX 변환

```bash
python3 scripts/convert_hwp.py input.hwp -o output.hwpx
```

### 마크다운 → HWPX 문서 생성

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("scripts")))
from hwpx_helpers import *

# section0.xml 조립 → build_hwpx.py로 빌드 → fix_namespaces.py 후처리
```

### 양식 복제

```bash
# 분석
python3 scripts/clone_form.py --analyze sample.hwpx

# 복제 + 텍스트 치환
python3 scripts/clone_form.py sample.hwpx output.hwpx --map replacements.json
python3 scripts/fix_namespaces.py output.hwpx
```

### 텍스트 추출

```bash
python3 scripts/text_extract.py doc.hwpx
python3 scripts/text_extract.py doc.hwpx --format markdown
```

## 프로젝트 구조

```
hwpx-skill/
├── SKILL.md                    # 스킬 전체 문서 (Decision Tree, 워크플로우, 규칙)
├── scripts/
│   ├── hwpx_helpers.py         # 헬퍼 라이브러리 (배너/섹션바/이미지/빌드)
│   ├── convert_hwp.py          # HWP→HWPX 변환
│   ├── build_hwpx.py           # 템플릿+XML → .hwpx 조립
│   ├── fix_namespaces.py       # 네임스페이스 후처리 (필수)
│   ├── clone_form.py           # 양식 복제
│   ├── md2hwpx.py              # 마크다운→HWPX 변환
│   ├── analyze_template.py     # HWPX 심층 분석
│   ├── verify_hwpx.py          # 품질 검증
│   ├── validate.py             # 구조 검증
│   ├── text_extract.py         # 텍스트 추출
│   ├── create_document.py      # 문서 생성
│   └── office/                 # unpack/pack 유틸리티
├── templates/                  # 문서 템플릿
│   ├── base/                   # 베이스 skeleton
│   ├── report/                 # 보고서
│   ├── gonmun/                 # 공문
│   ├── minutes/                # 회의록
│   ├── proposal/               # 제안서
│   └── government/             # 관공서 (컬러 배너/섹션 바)
├── assets/                     # 레퍼런스 템플릿
└── references/                 # 기술 문서
```

## HWP→HWPX 변환 지원 범위

| 항목 | 지원 |
|------|------|
| 텍스트 | O |
| 표 | O |
| 이미지 (PNG/JPG/BMP/GIF) | O |
| 도형 (사각형/원/선) | O |
| 컨테이너 (그룹 도형) | O |
| 각주/미주 | O |
| 다단 | O |
| 머리말/꼬리말 | O |
| OLE 객체 | 부분 지원 |
| 수식 | 미지원 |

## 주요 규칙

1. 모든 빌드 후 `fix_namespaces.py` 필수 실행
2. `.hwp` 파일은 워크플로우 H로 HWPX 변환 후 처리
3. 양식 복제 시 `clone_form.py` 사용 (XML 직접 조작 금지)
4. 템플릿 간 스타일 ID 호환 불가 — 해당 템플릿 ID만 사용
5. `mimetype`은 첫 ZIP 엔트리, `ZIP_STORED`

## 관련 프로젝트

- [hwp2hwpx-python-refactor](https://github.com/jkf87/hwp2hwpx-python-refactor) — HWP→HWPX 변환 엔진

## 라이선스

MIT
