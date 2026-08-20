#!/usr/bin/env python3
"""
HWPX 문서 생성 헬퍼 함수 라이브러리.

문단·이미지 등 HWPX 조립에 필요한 저수준 빌드 블록을 제공한다.

주의: 각 함수의 charPr/paraPr 기본값은 예시일 뿐이다. 실제로는 사용하는
템플릿의 ID 를 인자로 넘겨야 한다(템플릿 간 ID 는 호환되지 않는다).

사용법:
    from hwpx_helpers import *
    # 또는
    exec(open("${CLAUDE_SKILL_DIR}/scripts/hwpx_helpers.py").read())
"""

import os
import re
import zipfile

# --- 네임스페이스 선언 (section0.xml 루트에 사용) ---
NS_DECL = (
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
    'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
    'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
    'xmlns:epub="http://www.idpf.org/2007/ops" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
)

# --- ID 카운터 ---
_id_counter = 0


def next_id():
    """문서 내 고유 ID 생성."""
    global _id_counter
    _id_counter += 1
    return str(_id_counter)


def reset_id(start=0):
    """ID 카운터 리셋."""
    global _id_counter
    _id_counter = start


def xml_escape(text):
    """XML 특수문자 이스케이프."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# --- secPr 추출 ---
def extract_secpr_and_colpr(hwpx_path):
    """레퍼런스 HWPX에서 secPr + colPr 블록 추출."""
    with zipfile.ZipFile(hwpx_path, "r") as z:
        data = z.read("Contents/section0.xml").decode("utf-8")
    m = re.search(r"<hp:secPr.*?</hp:secPr>", data, re.DOTALL)
    secpr = m.group() if m else ""
    end = m.end() if m else 0
    ctrl_m = re.search(r"<hp:ctrl>.*?</hp:ctrl>", data[end:end + 500], re.DOTALL)
    colpr = ctrl_m.group() if ctrl_m else ""
    return secpr, colpr


# --- 기본 문단 생성 ---
def make_first_para(secpr, colpr, charpr="25", parapr="40"):
    """첫 문단 (secPr + colPr 포함, 필수)."""
    p_id = next_id()
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{charpr}">'
        f'{secpr}{colpr}'
        f'</hp:run></hp:p>'
    )


def make_empty_line(charpr="41", parapr="18"):
    """빈 줄."""
    p_id = next_id()
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{charpr}"><hp:t/></hp:run></hp:p>'
    )


def make_page_break(charpr="41", parapr="18"):
    """강제 페이지 넘김."""
    p_id = next_id()
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" '
        f'pageBreak="1" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{charpr}"><hp:t/></hp:run></hp:p>'
    )


def make_text_para(text, charpr, parapr):
    """텍스트 문단."""
    p_id = next_id()
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{charpr}"><hp:t>{xml_escape(text)}</hp:t></hp:run></hp:p>'
    )


def make_body_para(marker, text, marker_charpr="18", text_charpr="38", parapr="4"):
    """본문 문단: 볼드 마커 + 일반 내용. (예: "가. 내용텍스트")"""
    p_id = next_id()
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{marker_charpr}"><hp:t>{xml_escape(f"  {marker} ")}</hp:t></hp:run>'
        f'<hp:run charPrIDRef="{text_charpr}"><hp:t>{xml_escape(text)}</hp:t></hp:run></hp:p>'
    )


# --- 이미지 문단 ---
def make_image_para(binary_item_id, width=40000, height=22500, parapr="19"):
    """
    이미지 문단. 전체 hp:pic 필수 구조 포함.
    width, height: HWPUNIT 단위 (기본 16:9 = 40000×22500).
    """
    p_id = next_id()
    pic_id = next_id()
    inst_id = next_id()
    cx, cy = width // 2, height // 2
    return (
        f'<hp:p id="{p_id}" paraPrIDRef="{parapr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">'
        f'<hp:pic id="{pic_id}" zOrder="0" numberingType="PICTURE" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'href="" groupLevel="0" instid="{inst_id}" reverse="0">'
        f'<hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{width}" height="{height}"/>'
        f'<hp:curSz width="{width}" height="{height}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="0"/>'
        f'<hp:renderingInfo>'
        f'<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'</hp:renderingInfo>'
        f'<hc:img binaryItemIDRef="{binary_item_id}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
        f'<hp:imgRect>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{width}" y="0"/>'
        f'<hc:pt2 x="{width}" y="{height}"/><hc:pt3 x="0" y="{height}"/>'
        f'</hp:imgRect>'
        f'<hp:imgClip left="0" right="{width}" top="0" bottom="{height}"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{width}" dimheight="{height}"/>'
        f'<hp:effects/>'
        f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="{height}" heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="CENTER" '
        f'vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f'</hp:pic><hp:t/></hp:run></hp:p>'
    )


# --- 이미지 ZIP 추가 ---
def add_images_to_hwpx(hwpx_path, images):
    """images: [{"file": "photo.jpg", "id": "img1", "src_path": "/abs/path"}]"""
    tmp = str(hwpx_path) + ".img_tmp"
    with zipfile.ZipFile(hwpx_path, "r") as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "mimetype":
                    zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(item, data)
            for img in images:
                zout.write(img["src_path"], f"BinData/{img['file']}")
    os.replace(tmp, str(hwpx_path))


def update_content_hpf(hwpx_path, images):
    """content.hpf에 이미지 항목 등록."""
    tmp = str(hwpx_path) + ".hpf_tmp"
    with zipfile.ZipFile(hwpx_path, "r") as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "Contents/content.hpf":
                    text = data.decode("utf-8")
                    items = ""
                    for img in images:
                        ext = img["file"].rsplit(".", 1)[-1].lower()
                        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                                "png": "image/png", "bmp": "image/bmp"}[ext]
                        items += (f'<opf:item id="{img["id"]}" '
                                  f'href="BinData/{img["file"]}" '
                                  f'media-type="{mime}" isEmbeded="1"/>')
                    text = text.replace("</opf:manifest>", items + "</opf:manifest>")
                    data = text.encode("utf-8")
                if item.filename == "mimetype":
                    zout.writestr(item, data, compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(item, data)
    os.replace(tmp, str(hwpx_path))
