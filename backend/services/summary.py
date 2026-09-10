"""검색 결과를 3~4문장짜리 줄글 요약으로 만든다.

통계적 참고 문구로만 구성하고("~로 보입니다", "~일 수 있습니다"), 매수/매도를 직접
지시하는 단정적 표현은 쓰지 않는다 — 투자 자문이 아니라는 페이지 전체 원칙과 맞춘다.
"""


def _fmt_won(v10k: float | None) -> str:
    if v10k is None:
        return "정보 없음"
    v10k = round(v10k)
    eok, man = divmod(v10k, 10000)
    if eok:
        return f"{eok}억 {man:,}만원" if man else f"{eok}억원"
    return f"{man:,}만원"


def _pick_representative(valuations: list[dict]) -> dict | None:
    candidates = [v for v in valuations if v.get("fair_price_10k")]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.get("sample_count_3y") or 0)


def build_summary_text(
    complex_name: str,
    sigungu: dict,
    basis_info: dict | None,
    valuations: list[dict],
    manual_listings: list[dict] | None,
) -> str | None:
    if not valuations:
        return None

    rep = _pick_representative(valuations)
    if not rep:
        return None

    sentences = []

    # 1) 단지 개요
    overview_bits = []
    if basis_info and basis_info.get("household_cnt"):
        overview_bits.append(f"총 {basis_info['household_cnt']:,}세대")
    if basis_info and basis_info.get("use_approval_date"):
        year = str(basis_info["use_approval_date"])[:4]
        if year.isdigit():
            overview_bits.append(f"{year}년 준공")
    overview_str = ", ".join(overview_bits)
    loc = f"{sigungu['sido']} {sigungu['sigungu']}"
    if overview_str:
        sentences.append(f"{complex_name}은 {loc}에 위치한 {overview_str} 아파트입니다.")
    else:
        sentences.append(f"{complex_name}은 {loc}에 위치한 아파트입니다.")

    # 2) 대표 평형 가격 동향
    price_bit = (
        f"{rep['pyeong']}평(전용 {rep['avg_exclusive_area']}㎡) 기준 최근 3년 실거래가로 "
        f"추정한 적정가는 {_fmt_won(rep['fair_price_10k'])}이며, "
        f"직전 거래는 {_fmt_won(rep['last_deal_price_10k'])}({rep.get('last_deal_date') or '-'})"
        "입니다."
    )
    sentences.append(price_bit)

    if rep.get("is_breakout"):
        sentences.append("최근 거래가 최근 3년 내 최고가를 경신하며 상승 흐름을 보이고 있습니다.")
    elif rep.get("gap_to_peak_pct") is not None and rep["gap_to_peak_pct"] <= -10:
        sentences.append(
            f"최근 거래가는 최근 3년 최고가 대비 약 {abs(rep['gap_to_peak_pct'])}% 낮은 수준입니다."
        )

    # 3) 전세가율
    if rep.get("jeonse_ratio_pct") is not None:
        ratio = rep["jeonse_ratio_pct"]
        if ratio >= 60:
            tone = "높은 편으로, 매매가 상승 여력을 함께 보는 시각도 있습니다"
        elif ratio >= 45:
            tone = "중간 수준입니다"
        else:
            tone = "낮은 편으로, 매매가 대비 갭이 큰 편입니다"
        sentences.append(f"전세가율은 약 {ratio}%로 {tone}.")

    # 4) 호가 대비 판단 (수동 입력이 있는 평형이 있으면 반영)
    listing_by_label = {
        m["pyeong_label"]: m for m in (manual_listings or []) if m.get("ask_price_min") or m.get("ask_price_max")
    }
    matched_listing = None
    for v in valuations:
        label = f"{v['pyeong']}평 (전용 {v['avg_exclusive_area']}㎡)"
        if label in listing_by_label:
            matched_listing = (v, listing_by_label[label])
            break

    if matched_listing:
        v, listing = matched_listing
        ask_min, ask_max = listing.get("ask_price_min"), listing.get("ask_price_max")
        ask = (ask_min + ask_max) / 2 if ask_min and ask_max else (ask_min or ask_max)
        fair = v.get("fair_price_10k")
        if fair and ask:
            diff_pct = (fair - ask) / fair * 100
            if diff_pct >= 5:
                sentences.append(
                    f"입력하신 호가는 추정 적정가 대비 약 {diff_pct:.1f}% 낮아 급매 가능성이 있어 보입니다."
                )
            elif diff_pct >= -3:
                sentences.append("입력하신 호가는 추정 적정가와 비슷한 수준입니다.")
            else:
                sentences.append(
                    f"입력하신 호가는 추정 적정가 대비 약 {abs(diff_pct):.1f}% 높아 협상 여지를 검토해볼 만합니다."
                )
    else:
        sentences.append(
            "현재 매물 호가를 입력하면 추정 적정가와 비교해 급매·협상 여지를 함께 보여드립니다."
        )

    return " ".join(sentences)
