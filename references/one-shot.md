# 원샷 입력·출력 계약

`scripts/one_shot.py`는 v1과 v2를 같은 `schemas/one-shot.schema.json`으로 실제 검증한다. `--schema`와 `--example`이 실행 계약의 기준이다.

## 최소 실행

명령은 스킬 루트 기준이다. 산출물 경로는 명세 위치를 기준으로 해석한다.

```text
python scripts/one_shot.py --example official-letter
python scripts/one_shot.py --example brief-report
python scripts/one_shot.py --example plan-report
python scripts/one_shot.py --example diagram
python scripts/one_shot.py --example process-summary
python scripts/one_shot.py /absolute/path/spec.json --report /absolute/path/quality.json
```

예시 출력 내용을 파일 작성 도구로 JSON에 저장한다. PowerShell 버전에 따라 리다이렉션 인코딩이 다르므로 UTF-8로 저장한다. 실행 의존성은 `requirements-one-shot.txt`에 있으며, 한컴 페이지 검토에는 한컴오피스와 Poppler도 필요하다. 한컴이 없는 macOS·Linux의 근사 렌더(`render_hwpx.py --engine rhwp`)에는 Node.js와 `rsvg-convert`(macOS는 내장 `sips`로 대체 가능)가 필요하다.

## v1 호환 입력

- `official-letter`: `document` 필수. 기관명·수신·제목·발신명의와 비어 있지 않은 body 문자열 배열을 요구한다. 붙임은 문자열 배열, 결재는 직위·성명·일자 객체 배열이다.
- 나머지 유형: `markdown` 또는 `source` 중 하나. 한 개의 문서 제목과 실질적인 본문이 필요하다.
- `markdown` 유형만 `template`을 선택한다: base/gonmun/report/minutes/proposal.
- 공문 중첩 오타·null 본문·문자열 불리언 등 과거에 잘못 묵인된 입력은 호환 대상으로 보지 않는다.
- 그림은 독립된 `![설명](local-file.png)` 블록으로 넣는다. PNG/JPEG/BMP를 지원한다. 없는 그림, 원격 URL, 미지원 하이퍼링크·각주·수식·HTML은 조용히 삭제하지 않고 오류를 반환한다.

## v2 확장

v1의 입력 방식도 사용 가능하다. 구조화된 보고서는 `title`과 `blocks`를 사용하며 markdown/source와 동시에 지정하지 않는다.

- 블록: paragraph, heading, quote, list, table, image, diagram, pagebreak.
- `facts`: 이름 → 필수 표시 문구. 요청에서 확인한 날짜·시간·참가비 등이며, 원고와 결과에 모두 존재해야 한다.
- `unknowns`: 미확정 사항 배열. 비어 있지 않으면 draft가 true여야 한다.
  문서 완성에 필요한 미정 사실만 기록한다. 일반 지식 보고서에서 생략하는 기관·작성일은 미정 필수 사실이 아니다. 실제 일정의 필수 날짜가 미정인 경우와 구별한다.
- `purpose: cover`: 의도적으로 표지만 요청한 경우의 제목-only 예외. 일반 계획서를 이 값으로 우회하지 않는다.
- `metadata`: 생성기에서 지원하는 프런트매터 키·문자열 값. 미지원 키는 거부한다.
- `profile`: default(기존 형식), neutral, public. `profiles/document.json`에서 관리한다.
- `style`: 선택적 font, body_size(9–18pt), accent(6자리 RGB). 설치된 글꼴을 사용하고 실제 페이지에서 확인한다.
  body_size는 본문 역할만 바꾼다. 요약형의 일반 표 13pt, 도식 11pt, 주석 등은 일괄 축소되지 않는다. 본문을 줄여도 큰 표의 지면은 줄지 않을 수 있다.
- 표 도식의 상세 제한과 이미지 생성 연계는 [visual-elements.md](visual-elements.md)를 따른다.

코드·로그가 긴 새 범용 보고서에는 `kind: markdown`, `template: report`, `style.layout: technical-report`를 선택할 수 있다. 제목의 영문 단어를 유지하고, 짧은 코드 블록은 가능한 한 묶고 긴 로그는 페이지를 넘겨 배치한다. 기본 본문은 맑은 고딕 11pt, 코드의 영문은 Consolas 9pt이며 `style.font`, `body_size`, `accent`, `code_font`로 바꿀 수 있다. `code_font`는 이 조판에서만 지원한다. 다른 유형·기존 양식에는 자동 적용하지 않는다. 실제 설치 글꼴과 쪽 배치는 렌더로 확인한다. 자세한 입력·한계는 [source-report.md](source-report.md)를 따른다.

## 날짜·초안·문체

`metadata_date`는 YYYY-MM-DD 패키지 날짜다. 네 생성기에 동일하게 적용한다. 생략한 고정값 2000-01-01은 재현성을 위한 메타데이터이지 실제 작성일 추정이 아니다. 본문의 행사일·보고일을 덮어쓰지 않는다.

draft가 true이면 제목에 [초안]을 붙이고 리포트에 draft/unknowns/unresolved 및 DRAFT 상태를 기록한다. 원본 사실이 부족하면 질문하거나 사용자 요청에 따른 초안으로 처리한다.

`quality.writing`은 required/advisory/off다. v1과 공문은 기본 required, v2 보고서는 advisory다. 문체 규칙은 표본 기반 관행이지 모든 기관의 의무 규정이 아니다. 경고는 줄 위치·규칙·수정 제안과 함께 보존한다.

## 검증 옵션

- `hancom`: auto/required/off. auto는 환경상 사용할 수 없으면 경고와 not_run, required는 실패, off는 명시적 미수행.
- `reject_placeholders`: 기본 true. 초안이 아닌 결과의 미해결 플레이스홀더를 거부한다.
- `required_text`: v1에서도 사용할 수 있는 필수 문구 배열.
- `fail_on_warnings`: 문체·레이아웃 경고까지 공개 차단할지 선택.
- `repair_layout`: 기본 false. true이면 세로 병합 없는 표 행의 높이만 최대 2회 증가시킨다. 모든 셀·표 높이를 함께 갱신하고 텍스트·그림 보존을 확인한다. 완전한 자동 조판기는 아니다.

## 저장과 결과

입력 명세·원고·그림·출력·리포트의 경로 충돌은 생성 전에 거부한다. `--output` 재정의와 기존 하드링크도 검사한다. 전처리 실패 시 안전한 리포트 경로를 확정하지 못했으면 JSON은 stdout에만 반환하며 report_saved는 false다.

검사를 통과한 HWPX 하나만 원자적으로 최종 경로에 교체한다. 리포트 저장은 별도 연산이다. 문서 저장 후 리포트 저장 실패면 `status: PARTIAL`, `published: true`, `report_saved: false`, 종료 코드 2로 실제 상태를 알린다.

리포트에는 다음을 구분한다.

- 입력·생성·검사 시간, 적용 프로필과 보정 이력
- 구조와 참조 무결성, 검사한 섹션·표·셀 수
- 순서 있는 본문/표 셀의 누락, 실제 삽입 이미지 해시 대조
- 문체 경고와 필수 문구, 초안·미확정 사항
- 한컴 열림, 페이지 렌더, 시각 검토 각각의 상태

렌더·시각 검토를 하지 않았으면 not_run이다. 정적 PASS가 페이지 품질 PASS를 의미하지 않는다. 렌더는 `render_hwpx.py`로 수행하고 실제 PNG를 확인한다.

v2 의미 블록의 디코딩된 글에 literal `\n`, `\r`, `\t`가 남으면 `checks.input_text.findings`에 JSON Pointer와 문맥을 경고한다. 의도된 코드·경로 설명일 수 있어 자동 치환하지 않는다. 파일 경로 자체는 대상에서 제외한다. `fail_on_warnings`를 요청한 경우 공개를 차단한다.

## 재현성과 평가 범위

같은 환경·명세·고정 자원에서는 같은 HWPX 바이트를 검사한다. 리포트의 실행 시간과 한컴 PDF 메타데이터는 바이트 재현성 대상이 아니다. 생성 이미지 자체의 재생성을 결정론적이라고 주장하지 않는다.

모델 간 최초 성공률은 엔진 테스트와 별도의 평가다. 모델명·추론 설정·입력·수정 횟수·페이지 품질·시간을 기록한 실제 평가 없이 작은 모델/큰 모델 모두의 품질을 보장하지 않는다.

고정 명세의 재생성은 다음 명령으로 별도 프로세스에서 확인한다. 입력 파일·그림·생성 코드/템플릿의 전후 해시와 각 빌드 로그를 새 폴더에 남긴다. 기존 산출물은 덮어쓰지 않는다.

```text
python scripts/verify_reproducibility.py /absolute/path/spec.json --output-dir /absolute/path/new-replay-folder --runs 3
```

승인한 HWPX의 해시가 있으면 `--expected-sha256 <64자리 SHA256>`도 사용한다. 결과의 실행 시간·PDF·PNG·한컴/글꼴 환경·원고를 새로 작성하는 모델 과정은 동일 바이트 보장 범위가 아니다. 검증 중 입력 변경이나 하나의 빌드 실패도 PASS로 처리하지 않는다.
