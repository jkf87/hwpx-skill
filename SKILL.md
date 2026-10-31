---
name: hwpx
description: "한글 HWP/HWPX 파일의 변환·읽기·편집과 편집 가능한 .hwpx 문서 생성. 한글 파일, HWP 변환, HWPX 공문·보고서·계획서·활동지, 기존 한글 양식 채우기 요청에 사용한다."
---

# 편집 가능한 고품질 한글 문서

모델은 요구 이해와 원고 구성에 집중하고, 코드는 입력 계약·자원·내용 보존·파일 저장을 검증한다. 같은 명세의 재현성과 서로 다른 모델의 결과 일관성은 별개다. 실제 모델 비교 없이 후자를 보장하지 않는다.

아스트라(Astra, `gpt-6-astra`)를 사용하는 Codex 환경을 지원한다. 집필·도식 설계·검토에 사용자 지정 모델과 추론 설정을 유지하며, 사용자 요청 없이 Astra로 전환하거나 추가 모델을 호출하지 않는다. Astra와 Luna 모두 같은 입력 계약·검사 경로를 사용한다.

## 새 문서

1. 사용자 요청의 사실·필수 조건·미확정 사항을 정리한다. 기관명·연도·장소·문서번호 등을 추측하지 않는다.
   - 문서에 필요하지만 미정인 사실과, 요청상 생략하는 항목은 다르다. 일반 지식 보고서에 기관·작성일을 쓰지 않는다는 이유만으로 unknowns나 초안 표시를 만들지 않는다.
2. 아래 문서 유형을 고르고 실행 가능한 예시와 스키마를 확인한다.
3. 기본 문서는 v1 Markdown/공문 JSON을 사용한다. 사실 계약·표 도식·기관별 표현이 필요하면 v2의 의미 블록을 사용한다. 모델이 XML이나 셀 좌표를 작성할 필요는 없다.
   - 공문 키는 예시의 `기관명`, `수신`, `제목`, `발신명의`, `body`를 그대로 사용한다. 영문 별칭을 추측하지 않는다.
   - 절차도·흐름도·추진체계도 요청은 `diagram` 블록과 `visual-elements.md`로 연결한다. 단계가 나열된 일반 데이터 표만으로 도식 요청을 충족했다고 판단하지 않는다.
4. 명세를 파일로 저장해 한 번 빌드한다. `ok`, `published`, `draft`, 미수행 검사 상태를 확인한다.
5. 완성본은 가능한 경우 실제 한컴 페이지를 렌더하고 육안 검토한다. 정적 통과, 한컴 열림, 렌더 성공, 시각 검토를 같은 것으로 취급하지 않는다.

아래 명령은 **이 SKILL.md가 있는 디렉터리**를 기준으로 한다. 다른 작업 디렉터리에서는 스크립트 경로를 해당 스킬의 절대 경로로 바꾼다. 특정 환경변수가 자동 설정된다고 가정하지 않는다.

```text
python scripts/one_shot.py --example official-letter
python scripts/one_shot.py --example diagram
python scripts/one_shot.py --schema
python scripts/one_shot.py /absolute/path/spec.json --report /absolute/path/quality.json
```

명세의 상대 경로는 명세 파일 위치 기준이며, `source` 원고 속 그림 경로는 원고 위치 기준이다. 출력은 .hwpx여야 한다. 명세·원고·그림·출력·리포트가 충돌하면 사전에 거부한다.

| 유형 | 입력과 용도 |
|---|---|
| `official-letter` | `document` 객체. 시행 공문·기안문, 붙임 안내·결재 필드 |
| `brief-report` | Markdown 또는 v2 블록. 결재선·제목 띠가 있는 요약보고 |
| `plan-report` | Markdown 또는 v2 블록. 장 배너·선택적 표지가 있는 계획·검토보고 |
| `research-plan` | 연구학교 연구계획서 완본(28쪽, 표지·실태분석·SWOT·추진체계도 도식·실행계획 표). `scripts/research_plan.py`로 복제·생성 |
| `markdown` | 범용 보고서·회의록·제안서. `template` 스타일 선택 가능 |

입력·결과 계약은 [one-shot.md](references/one-shot.md), v2 표 도식과 이미지 경로는 [visual-elements.md](references/visual-elements.md)를 읽는다. 예시는 가상 검증 자료이며 실제 기관 정보가 아니다.

짧은 **과정 요약 + 표 흐름도**를 새로 만들고 별도 양식·분기 그래프 요구가 없다면 [summary-workflow.md](references/summary-workflow.md)의 선택형 콘텐츠 레시피를 우선 검토한다. 내용만 작성하면 코드가 검증된 요약 배치와 연결을 구성한다. 복잡한 관계·긴 보고서·지정 서식은 기존 v2를 사용한다. 모든 문서에 같은 단계 수나 지면을 강제하지 않는다.

**원문을 축약하지 않는 상세 보고서**, 코드·로그가 긴 기술 문서, 승인된 보고서를 기준으로 집필을 지도하는 요청은 [source-report.md](references/source-report.md)를 읽는다. 원문 보존 조립기와 선택형 `technical-report` 조판을 제공하며, 특정 모델·장 수·도식 수·쪽 수를 강제하지 않는다.

## 품질 계약과 한계

- JSON Schema가 중첩 필드와 타입까지 검사한다. 미지원 기능이나 자원 누락은 조용히 버리지 않는다.
- 제목뿐 아니라 순서 있는 본문·표 셀·붙임 문구와 실제 삽입 이미지의 해시를 대조한다.
- v2 `facts`에는 요청에서 확인한 필수 사실을 기록한다. 원고와 산출물 양쪽에서 검사한다. 모델이 명세에서부터 누락한 사실은 이 검사만으로 알아낼 수 없다.
- ZIP·XML·스타일/글꼴/이미지 참조와 실제 표 셀을 검사한다. 폭·글자 크기 기반 높이 추정은 시각 검토의 대체가 아니다.
- `quality.repair_layout: true`는 병합되지 않은 표 행의 높이만 최대 2회 보정한다. 문구·글자 크기·관계를 바꾸지 않고, 보정 후 내용과 구조를 다시 검사한다. 기본값은 false다.
- 파일은 검사를 통과한 후보만 최종 위치로 원자적으로 교체한다. HWPX와 리포트 두 파일이 동시에 원자적으로 저장되는 것은 아니다.
- `draft: true` 결과에는 제목의 [초안] 표시와 리포트의 DRAFT 상태가 붙는다. 완성본으로 안내하지 않는다.
- `metadata_date`는 패키지 날짜이며 본문의 행사일·보고일과 별개다. 생략 시 고정 메타데이터 날짜를 사용하고 그 사실을 기록한다.
- 문체는 기관별 선택이다. v1은 기존 엄격 정책을 유지한다. v2 보고서는 기본 advisory이며 `quality.writing`으로 required/advisory/off를 명시할 수 있다. 파일·사실 검사는 이 선택으로 해제되지 않는다.
- 내장 양식은 현재 경로만 사용한다. 폐기된 브라더 보고서 및 교육청 체육과 양식을 백업·과거 배포본에서 자동 복원하지 않는다. 사용자가 특정 원본을 명시적으로 제공한 편집 작업은 별개다.

## 실제 페이지 확인 및 Visual QA

```text
python scripts/render_hwpx.py /absolute/path/output.hwpx --output-dir /absolute/path/new-empty-review-folder --expected-pages 1
```

독립 한컴 인스턴스에서 열고 PDF로 내보낸 뒤 Poppler(`pdftoppm`)로 PNG를 만든다. Poppler가 없으면 PDF와 쪽수만 만들고 `page_images_status: not_run`으로 표시하므로 PDF를 직접 확인한다. 모든 페이지의 제목 계층, 잘림·겹침, 연결 라벨, 표 분할, 불필요한 빈 페이지를 `view_file`로 직접 시각 검토(Visual QA)한다. 긴 라벨은 내용을 삭제하기보다 배치·방향·칸 크기를 조정하고 다시 검증한다.

- **페이지 밀림/폭발 방지**: 양식 원본의 빈 셀에 설정된 큰 `cellSz/@height`(예: 20000~30000 HWPUNIT)는 글자가 들어가면 페이지가 밀리는 주원인이다. 본문이 채워지는 셀의 높이는 1000 이하로 리셋하여 내용량에 맞게 자동 확장(auto-grow)되도록 조정한다.
- **불필요한 빈 문단 삭제**: 표 내부나 섹션의 잔여 빈 문단(`<hp:p>`)을 정리하고 줄간격(`lineSpacing`)을 130% 수준으로 조판한다.
- **사용자가 쪽 수를 지정한 경우**: `--expected-pages N`을 반드시 주어 실제 쪽 수가 불일치하면 즉시 실패 처리되도록 한다.

## 기존 파일 및 양식 처리

원본을 받으면 원본 보존 편집을 우선한다. 비대상 ZIP 엔트리·본문·그림을 보존한다.

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/convert_hwp_hancom.ps1 -InputPath input.hwp -OutputDirectory converted
python scripts/convert_hwp.py input.hwp -o converted.hwpx
python scripts/fill_hwpx.py analyze form.hwpx
python scripts/fill_hwpx.py fill form.hwpx filled.hwpx --values values.json
python scripts/fill_hwpx.py check filled.hwpx --strict
python scripts/research_plan.py --school "○○초등학교" --title "○○ 연구과제" -o 연구계획서.hwpx
```

- **HWP 변환 원칙 (중요)**: Windows 한컴 환경에서는 반드시 한컴 COM(`convert_hwp_hancom.ps1` 또는 Python `win32com.client` 기반 `SaveAs(..., "HWPX")`)을 최우선으로 사용한다. 파이썬 내장 변환기(`convert_hwp.py`)는 복잡한 표/글상자가 포함된 HWP 문서 변환 시 한컴오피스에서 열리지 않는 비표준 HWPX를 생성할 수 있다.
- **텍스트 겹침 방지 (`linesegarray` 필수 제거)**: 한컴이 생성한 HWPX에는 이전 줄바꿈 좌표 캐시인 `<hp:linesegarray>`가 포함되어 있다. 텍스트를 교체/추가할 때 이를 제거하지 않으면 글자가 한 줄에 겹쳐서 출력되는 심각한 버그가 발생한다. 편집 후 반드시 전수 제거한다.
- **문단 전체 치환**: 문단 텍스트 교체 시 일부 `run/t`만 바꾸면 잔여 텍스트와 합쳐져 중복 표기되므로, 기존 `run`과 `linesegarray`를 비우고 단일 `run/t`를 새로 생성한다.

원샷 밖에서 생성·수정했다면 전달 전 다음 검사와 페이지 검토를 수행한다.

```text
python scripts/fix_namespaces.py output.hwpx
python scripts/finalize_hwpx.py output.hwpx --strip-linesegarray --layout
python scripts/validate.py output.hwpx --layout
python scripts/fill_hwpx.py check output.hwpx --strict
python scripts/finalize_hwpx.py output.hwpx --hancom
```

## 작업별 참고

| 요청 | 경로 |
|---|---|
| 공문 표기 | [gonmunseo-2025-writing-rules.md](references/gonmunseo-2025-writing-rules.md) |
| 요약보고 | [yoyak-bogo-style.md](references/yoyak-bogo-style.md) |
| 계획·검토보고 | [geomto-bogo-style.md](references/geomto-bogo-style.md) |
| 범용 보고서 | [report-style.md](references/report-style.md) |
| HTML 활동지 | `html2hwpx.py`, [html-to-hwpx.md](references/html-to-hwpx.md) |
| 복잡한 양식·표·각주·수식·직인·원본 편집 | [advanced-workflows.md](references/advanced-workflows.md)의 해당 워크플로우 |
| 레퍼런스 재현 | `doc_spec.py`, 고급 참고서 워크플로우 R |
| 읽기·텍스트 추출 | `text_extract.py`, 고급 참고서 워크플로우 E |

`doc_spec.py render`는 HWPX 조립이며 페이지 렌더러가 아니다. 필요한 참고서를 더 읽는 횟수를 제한하지 않는다. 일반 새 문서에는 이 메인 경로가 과거 고급 예제보다 우선한다.

## 결과 전달

절대 경로의 HWPX 링크, 적용한 형식, 실제 수행한 검사와 미수행 항목을 간결하게 알린다. `published: false`인 후보를 완성본으로 전달하지 않는다. PARTIAL은 문서 저장은 됐으나 리포트 저장 등에 실패한 상태다. 실패·초안·시각 검토 미완료를 숨기지 않는다.
