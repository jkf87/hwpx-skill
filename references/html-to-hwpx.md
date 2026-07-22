# K-Teacher 학생 활동지 HTML → HWPX

`scripts/html2hwpx.py`는 K-Teacher 학생 활동지 HTML을 스크린샷이 아닌 편집 가능한 HWPX 표와 문단으로 변환한다.

## Pipeline

```text
HTML → design-plan.xml → section0.xml → HWPX → namespace/finalization/layout validation
```

범용 CSS 렌더러가 아니라 원본 활동지의 의미 컴포넌트를 결정론적으로 매핑한다. 시각 토큰과 품질 기준은 저장소 루트 `DESIGN.md`가 기준이다.

## Supported HTML

- `.doc-header` 안의 `h1`과 선택적 `.subtitle`
- `.page`, `.page-run`, `.footer`
- `section.block > h2`와 본문
- `p[data-block-type="student_task"]`
- `p[data-block-type="student_note"]`
- `p[data-block-type="source_card"]`
- `p[data-block-type="sentence_support"]`
- `p[data-block-type="exit_ticket"]`
- `table[data-block-type="data_table"]`
- `table[data-block-type="fill_table"]`
- `table[data-block-type="answer_box"]`
- `p[data-block-type="page_break"]`
- 일반 `p`, `h3`, `ul`, `ol`, `table`

표의 `caption`과 `tr[data-row-height-mm]`은 중간 XML에 보존된다.

## Command

```bash
python3 scripts/html2hwpx.py input.html output.hwpx \
  --keep-xml build/html2hwpx \
  --creator "작성자"
```

`--keep-xml`은 정규화된 디자인 계획, 커스텀 헤더 스타일, 생성된 OWPML 섹션을 남긴다.

## Design mapping

| HTML | HWPX |
|---|---|
| 문서 머리 | 흰 제목 셀 + 남색 `WORKSHEET` 배지 셀의 2단 표 |
| 학습자 정보 | 영문 라벨과 한글 필드가 함께 있는 5열 정보 스트립 |
| 섹션 제목 | 청록 번호 셀 + 연녹 제목 셀의 섹션 레일 |
| 목표 | 둥근 연청 카드 + teal `MISSION` 라벨 |
| 학생 과제 | 둥근 연청 카드 + cobalt `STEP 01/02` 라벨 |
| 자료 카드 | 둥근 연녹 카드 + `SOURCE NOTE` 라벨 + 청록 테두리 |
| 표 | 남색 머리행 + 흰 글씨 + 교차 행 배경 + 회청 격자 |
| 답안 상자 | 바깥 카드 없이 아래선이 있는 8mm 빈 행 |
| 출구표 | 둥근 연주황 카드 + coral 테두리 + amber 라벨 |
| 쪽 나누기 | `hp:p pageBreak="1"` |

HWPX 표 셀에는 CSS식 `border-radius`가 없으므로, 목표·과제·자료·출구표 카드는 네이티브 `hp:rect` 글상자의 `ratio`로 라운드를 표현한다. 구조화된 표와 답안선은 편집성과 페이지 안정성을 위해 표로 유지한다. 그림자는 인쇄 품질을 위해 생략한다.
