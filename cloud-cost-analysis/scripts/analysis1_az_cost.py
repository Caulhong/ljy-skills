"""
分析1：同 Region 不同 AZ 成本差异 + 非算力成本拆解

分析层次：
  1. region+资源类型 维度：计算每个 (region, 资源类型) 组合 az1 vs 其他 AZ 的单台成本差距
     并找出驱动差异的具体成本科目（DC/DCN/网络）
  2. region 维度：统计有多少比例的资源类型存在显著 az1 溢价 → 系统性 or 个别问题
  3. 动态展示：
     - 系统性 region（>70%资源类型偏高）：只展示 region 汇总，不展 resource-type 明细
     - 个别/混合 region（≤70%）：额外展示 resource-type 明细（至多3行）并给出成本根因

输入：df DataFrame（所有阶段数据）
输出：dict {
    'chart': base64 图片字符串（成本堆叠图）,
    'dc_prem_rows': region 级 az1 溢价汇总表 HTML（max 3行）,
    'rtype_gap_table': region+资源类型 az1 溢价明细 HTML（仅个别/混合region，max 3行）,
    'az1_insight': 整体结论文字,
    'diff_insight': 系统性 vs 个别差异判断文字,
    'migration_hint': 牵引建议文字,
    'focus_regions': 选中的 region 列表,
}
"""
import io, base64, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import rcParams, font_manager

# 注册 Noto Sans SC（与报告 HTML 字体一致）；找不到时自动 fallback
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'assets', 'fonts', 'NotoSansSC-Regular.otf')
if os.path.exists(_FONT_PATH):
    font_manager.fontManager.addfont(_FONT_PATH)

rcParams['font.sans-serif'] = ['Noto Sans SC', 'Microsoft YaHei', 'SimHei',
                                'Hiragino Sans GB', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False
rcParams['axes.spines.top']    = False
rcParams['axes.spines.right']  = False
rcParams['axes.labelcolor']    = '#475569'
rcParams['xtick.color']        = '#475569'
rcParams['ytick.color']        = '#475569'
rcParams['xtick.labelsize']    = 9
rcParams['ytick.labelsize']    = 8.5

GAP_THRESH = 0.10   # az1 比其他 AZ 高出 10% 视为显著偏高
COMPONENT_THRESH = 0.05  # 成本科目差距 >5% 才算贡献


FIG_BG   = '#F8FAFC'   # 报告背景色，与模板 --bg 一致
NAV_DARK = '#1E3A5F'   # 报告 --navy
SPINE_C  = '#E2E8F0'   # 报告 --border
TEXT2    = '#475569'   # 报告 --text2


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor=FIG_BG)
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def _style_ax(ax, ylabel=False):
    """统一轴样式：与报告视觉系统保持一致。"""
    ax.set_facecolor('white')
    for spine in ['left', 'bottom']:
        ax.spines[spine].set_color(SPINE_C)
        ax.spines[spine].set_linewidth(0.8)
    ax.yaxis.grid(True, linestyle='--', linewidth=0.7, alpha=0.6, color=SPINE_C)
    ax.set_axisbelow(True)
    ax.set_xlabel('可用区', fontsize=9, color=TEXT2, labelpad=6)
    if ylabel:
        ax.set_ylabel('单台成本（万元/月）', fontsize=9, color=TEXT2, labelpad=6)
    else:
        ax.set_ylabel('')


def _root_cause(az1_row, oth_row):
    """比较 az1 vs 其他AZ 各成本科目的差距，返回最大驱动科目及绝对差值。
    用绝对差值排序避免折旧归零时相对溢价失真（分母≈0导致百分比虚高）。
    包含服务器折旧（算力）、DC、DCN、网络设备四个科目。"""
    components = {
        '服务器折旧': (az1_row.get('uc_dep', 0), oth_row.get('uc_dep', 0)),
        'DC成本':     (az1_row.get('uc_dc',  0), oth_row.get('uc_dc',  0)),
        'DCN成本':    (az1_row.get('uc_dcn', 0), oth_row.get('uc_dcn', 0)),
        '网络设备':   (az1_row.get('uc_net', 0), oth_row.get('uc_net', 0)),
    }
    best_name, best_abs, best_rel = '', 0.0, 0.0
    for name, (az1_v, oth_v) in components.items():
        abs_gap = az1_v - oth_v
        rel_gap = abs_gap / oth_v if oth_v > 0 else 0.0
        if abs_gap > best_abs:
            best_abs, best_rel, best_name = abs_gap, rel_gap, name
    return best_name, best_rel, best_abs


def run(df):
    """
    df: 所有阶段 DataFrame，需包含列：
        region, az, resource_type, stage, server_count, cost, unit_cost,
        server_depreciation_cost, dc_cost, dcn_cost, network_cost
    返回 dict。
    """
    df = df.copy()
    df['uc_dep'] = df['server_depreciation_cost'] / df['server_count']
    df['uc_dc']  = df['dc_cost']      / df['server_count']
    df['uc_dcn'] = df['dcn_cost']     / df['server_count']
    df['uc_net'] = df['network_cost'] / df['server_count']

    # ── Step 1: region+资源类型 维度，计算 az1 vs 其他AZ 各成本差距 ─────────────
    rtype_rows = []
    for (region, rtype), grp in df.groupby(['region', 'resource_type']):
        az1 = grp[grp['az'] == 'az1']
        oth = grp[grp['az'] != 'az1']
        if az1.empty or oth.empty:
            continue
        az1_uc  = az1['unit_cost'].mean()
        oth_uc  = oth['unit_cost'].mean()
        az1_vals = {c: az1[c].mean() for c in ['uc_dep', 'uc_dc', 'uc_dcn', 'uc_net']}
        oth_vals = {c: oth[c].mean() for c in ['uc_dep', 'uc_dc', 'uc_dcn', 'uc_net']}
        if oth_uc <= 0:
            continue
        gap_pct = (az1_uc - oth_uc) / oth_uc
        cause_name, cause_rel, cause_abs = _root_cause(az1_vals, oth_vals)
        rtype_rows.append({
            'region': region, 'resource_type': rtype,
            'az1_uc': az1_uc, 'oth_uc': oth_uc, 'gap_pct': gap_pct,
            'sig': gap_pct > GAP_THRESH,
            'cause': cause_name, 'cause_gap': cause_rel, 'cause_abs': cause_abs,
            'az1_age': az1['server_avg_age'].mean(),
            'oth_age': oth['server_avg_age'].mean(),
        })

    rtype_df = pd.DataFrame(rtype_rows) if rtype_rows else pd.DataFrame(
        columns=['region', 'resource_type', 'az1_uc', 'oth_uc', 'gap_pct', 'sig',
                 'cause', 'cause_gap', 'az1_age', 'oth_age'])

    # ── Step 2: 选 focus_regions（az1 显著偏贵的资源类型数量最多的 3 个 region）─
    if len(rtype_df) > 0:
        sig_count = rtype_df[rtype_df['sig']].groupby('region').size()
        focus_regions = sig_count.nlargest(3).index.tolist()
        if len(focus_regions) < 3:
            avg_gap = rtype_df.groupby('region')['gap_pct'].mean()
            extras = avg_gap[~avg_gap.index.isin(focus_regions)].nlargest(
                3 - len(focus_regions)).index.tolist()
            focus_regions += extras
    else:
        focus_regions = df['region'].unique()[:3].tolist()

    # ── Step 3: 各 region 系统性比例 ─────────────────────────────────────────
    focus_rtype = rtype_df[rtype_df['region'].isin(focus_regions)]
    region_sys = {}
    for region in focus_regions:
        reg = focus_rtype[focus_rtype['region'] == region]
        region_sys[region] = reg['sig'].mean() if len(reg) > 0 else 0.0

    # ── 成本堆叠图 ────────────────────────────────────────────────────────────
    stk_cols   = ['uc_dep', 'uc_dc', 'uc_dcn', 'uc_net']
    stk_colors = ['#3B82F6', '#EF4444', '#F59E0B', '#10B981']
    stk_labels = ['服务器折旧（算力）', 'DC成本', 'DCN成本', '网络设备']

    fig, axes = plt.subplots(1, 3, figsize=(13, 5))
    fig.patch.set_facecolor(FIG_BG)
    fig.suptitle('分析一：AZ 单台成本构成对比（全阶段）\nDC成本是AZ间差异的主因；折旧随年限归零',
                 fontsize=12, fontweight='bold', color=NAV_DARK, y=1.01)

    az1_dc_premium    = {}
    az1_total_premium = {}
    region_root_cause = {}

    for ax, region in zip(axes, focus_regions):
        sub   = df[df['region'] == region]
        az_bd = sub.groupby('az')[stk_cols].mean().sort_index()
        azs   = az_bd.index.tolist()
        bottom = np.zeros(len(azs))

        for col, color, label in zip(stk_cols, stk_colors, stk_labels):
            vals = az_bd[col].values
            bars = ax.bar(azs, vals, bottom=bottom, color=color, width=0.52,
                          edgecolor='white', linewidth=1.2, label=label)
            if col in ('uc_dep', 'uc_dc'):
                for bar, val, bot in zip(bars, vals, bottom):
                    if val > 0.05:
                        ax.text(bar.get_x() + bar.get_width() / 2, bot + val / 2,
                                f'{val:.2f}', ha='center', va='center',
                                fontsize=8.5, color='white', fontweight='bold')
            bottom += vals

        total = az_bd.sum(axis=1)
        dep   = az_bd['uc_dep']
        for i, az in enumerate(azs):
            nc_pct = (total[az] - dep[az]) / total[az] * 100 if total[az] > 0 else 0
            fc = '#DC2626' if az == 'az1' else TEXT2
            ax.text(i, total[az] + total.max() * 0.025,
                    f'非算力 {nc_pct:.0f}%', ha='center', va='bottom',
                    fontsize=8.5, color=fc,
                    fontweight='bold' if az == 'az1' else 'normal')

        ax.set_title(region, fontsize=10.5, fontweight='bold', color=NAV_DARK, pad=8)
        _style_ax(ax, ylabel=(ax == axes[0]))

        az1_dc  = sub[sub['az'] == 'az1']['uc_dc'].mean()
        oth_dc  = sub[sub['az'] != 'az1']['uc_dc'].mean()
        az1_tot = sub[sub['az'] == 'az1']['unit_cost'].mean()
        oth_tot = sub[sub['az'] != 'az1']['unit_cost'].mean()
        if oth_dc  > 0: az1_dc_premium[region]   = (az1_dc  - oth_dc)  / oth_dc  * 100
        if oth_tot > 0: az1_total_premium[region] = (az1_tot - oth_tot) / oth_tot * 100

        # region 级根因：找单台成本差距最大的成本科目（含折旧）
        az1_sub = sub[sub['az'] == 'az1']
        oth_sub = sub[sub['az'] != 'az1']
        region_az1_vals = {c: az1_sub[c].mean() for c in ['uc_dep', 'uc_dc', 'uc_dcn', 'uc_net']}
        region_oth_vals = {c: oth_sub[c].mean() for c in ['uc_dep', 'uc_dc', 'uc_dcn', 'uc_net']}
        rc_name, rc_rel, rc_abs = _root_cause(region_az1_vals, region_oth_vals)
        az1_age = az1_sub['server_avg_age'].mean()
        oth_age = oth_sub['server_avg_age'].mean()
        region_root_cause[region] = (rc_name, rc_rel, az1_age, oth_age)

    handles = [mpatches.Patch(color=c, label=l) for c, l in zip(stk_colors, stk_labels)]
    fig.legend(handles=handles, loc='upper right', fontsize=9,
               bbox_to_anchor=(1.0, 0.98), frameon=True, framealpha=0.9,
               edgecolor=SPINE_C, facecolor='white')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    chart_b64 = _fig_to_b64(fig)

    # ── 图表2：以表格里的 (region, 资源类型) 为单位，格式同图表1 ──────────────
    # 取显著偏高的前3个 (region, resource_type)，每个画一张子图
    # x轴 = AZ（az1/az2/az3），同图表1格式
    conc_regions_for_chart = [r for r in focus_regions if region_sys.get(r, 0) <= 0.7]
    detail_combos = (
        focus_rtype[focus_rtype['region'].isin(conc_regions_for_chart) & focus_rtype['sig']]
        .sort_values('gap_pct', ascending=False)
        .head(3)
        [['region', 'resource_type']]
        .values.tolist()
    )

    n_combos = len(detail_combos)
    if n_combos == 0:
        # 无个别资源池差异，生成空白占位图
        fig2, ax2 = plt.subplots(figsize=(6, 3))
        ax2.text(0.5, 0.5, '所有重点 Region 均为系统性差异\n无需资源类型明细', ha='center', va='center',
                 fontsize=11, color='#7F8C8D', transform=ax2.transAxes)
        ax2.axis('off')
        chart1b_b64 = _fig_to_b64(fig2)
    else:
        fig2, axes2 = plt.subplots(1, 3, figsize=(13, 5))
        fig2.patch.set_facecolor(FIG_BG)
        fig2.suptitle('分析一：资源类型维度 AZ 成本构成对比（显著偏高项）',
                      fontsize=12, fontweight='bold', color=NAV_DARK, y=1.01)

        for ax2, (region, rtype) in zip(axes2, detail_combos):
            grp   = df[(df['region'] == region) & (df['resource_type'] == rtype)]
            az_bd = grp.groupby('az')[stk_cols].mean().sort_index()
            azs   = az_bd.index.tolist()
            bottom2 = np.zeros(len(azs))

            for col, color, label in zip(stk_cols, stk_colors, stk_labels):
                vals = az_bd[col].values
                bars2 = ax2.bar(azs, vals, bottom=bottom2, color=color, width=0.52,
                                edgecolor='white', linewidth=1.2, label=label)
                if col in ('uc_dep', 'uc_dc'):
                    for bar2, val, bot in zip(bars2, vals, bottom2):
                        if val > 0.05:
                            ax2.text(bar2.get_x() + bar2.get_width() / 2, bot + val / 2,
                                     f'{val:.2f}', ha='center', va='center',
                                     fontsize=8.5, color='white', fontweight='bold')
                bottom2 += vals

            total2 = az_bd.sum(axis=1)
            dep2   = az_bd['uc_dep']
            for i, az in enumerate(azs):
                nc_pct = (total2[az] - dep2[az]) / total2[az] * 100 if total2[az] > 0 else 0
                fc = '#DC2626' if az == 'az1' else TEXT2
                ax2.text(i, total2[az] + total2.max() * 0.025,
                         f'非算力 {nc_pct:.0f}%', ha='center', va='bottom',
                         fontsize=8.5, color=fc,
                         fontweight='bold' if az == 'az1' else 'normal')

            gap_pct = rtype_df[
                (rtype_df['region'] == region) & (rtype_df['resource_type'] == rtype)
            ]['gap_pct'].values
            gap_str = f'  az1 +{gap_pct[0]*100:.1f}%' if len(gap_pct) > 0 else ''
            ax2.set_title(f'{region[-3:]} · {rtype}{gap_str}', fontsize=10.5,
                          fontweight='bold', color='#DC2626', pad=8)
            _style_ax(ax2, ylabel=(ax2 == axes2[0]))

        for ax2 in axes2[n_combos:]:
            ax2.set_visible(False)

        handles2 = [mpatches.Patch(color=c, label=l) for c, l in zip(stk_colors, stk_labels)]
        fig2.legend(handles2, stk_labels, loc='upper right', fontsize=9,
                    bbox_to_anchor=(1.0, 0.98), frameon=True, framealpha=0.9,
                    edgecolor=SPINE_C, facecolor='white')
        plt.tight_layout(rect=[0, 0, 1, 0.96])
        chart1b_b64 = _fig_to_b64(fig2)

    # ── 动态整体结论 ──────────────────────────────────────────────────────────
    dc_vals = list(az1_dc_premium.values())
    if dc_vals:
        avg_dc  = sum(dc_vals) / len(dc_vals)
        max_dc  = max(dc_vals)
        max_reg = max(az1_dc_premium, key=az1_dc_premium.get)
        if avg_dc < 2:
            az1_insight = (f"各重点 Region 的 az1 DC 成本与其他 AZ 差异极小（均值 {avg_dc:.1f}%），"
                           f"AZ 间成本基本均质，当前无明显迁移必要性。")
        elif avg_dc > 50:
            az1_insight = (f"az1 DC 成本溢价均值达 {avg_dc:.1f}%，最高达 {max_dc:.1f}%（{max_reg}），"
                           f"偏差异常偏大，建议核查数据准确性后再决策。")
        else:
            az1_insight = (f"az1 DC 成本高于同 region 其他 AZ 均值 <strong>{avg_dc:.1f}%</strong>，"
                           f"最高达 <strong>{max_dc:.1f}%</strong>（{max_reg}）。"
                           f"差异主要来自机房电力/制冷/空间条件。")

        az1_srv    = df[df['az'] == 'az1']['server_count'].sum()
        total_srv  = df['server_count'].sum()
        az1_share  = az1_srv / total_srv if total_srv > 0 else 0
        dc_in_cost = df['dc_cost'].sum() / df['cost'].sum()
        saving     = avg_dc * dc_in_cost * az1_share
        migration_hint = (
            f"若将 az1 新售全量切至低成本 AZ，预计可降低整体摊薄成本约 <strong>{saving*100:.1f}%</strong>。"
            if saving > 0.01 else
            "az1 台数占比较低，迁移收益有限，可结合具体场景评估。"
        )
    else:
        az1_insight    = "当前重点 Region 中未发现 az1 明显成本溢价。"
        migration_hint = ""
        az1_dc_premium    = {}
        az1_total_premium = {}

    # ── 系统性 vs 个别差异综合结论 ───────────────────────────────────────────
    if len(focus_rtype) > 0:
        overall_pct = focus_rtype['sig'].mean()
        region_tags = '，'.join(
            f"{r}（{'系统性' if v > 0.7 else '个别资源池' if v < 0.3 else '混合'}·{v*100:.0f}%）"
            for r, v in region_sys.items()
        )
        if overall_pct > 0.7:
            diff_insight = (
                f"重点 Region 平均 <strong>{overall_pct*100:.0f}%</strong> 的资源类型 az1 成本显著偏高，"
                f"属于<strong>系统性 AZ 差异</strong>，根因在机房基础设施（DC成本），与具体资源类型无关。"
                f"各 Region：{region_tags}。"
            )
        elif overall_pct < 0.3:
            diff_insight = (
                f"重点 Region 仅平均 <strong>{overall_pct*100:.0f}%</strong> 的资源类型 az1 成本明显偏高，"
                f"属于<strong>个别资源池问题</strong>，建议针对具体资源类型排查，而非全量 AZ 迁移。"
                f"各 Region：{region_tags}。"
            )
        else:
            diff_insight = (
                f"重点 Region 约 <strong>{overall_pct*100:.0f}%</strong> 的资源类型 az1 成本偏高，"
                f"<strong>系统性与个别差异并存</strong>，需分资源类型逐一确认根因。"
                f"各 Region：{region_tags}。"
            )
    else:
        diff_insight = "数据不足，无法判断 AZ 成本差异的系统性程度。"

    # ── 汇总表1：region 级（max 3行），含差异性质标签 ─────────────────────────
    def _badge(v):
        if v > 20:  return '🔴 建议新售切 az2/az3'
        if v > 5:   return '⚠️ 轻微溢价，持续观察'
        return '✅ AZ间差异不显著'

    def _sys_tag(region):
        pct = region_sys.get(region, 0)
        if pct > 0.7:   return f'系统性（{pct*100:.0f}%资源类型偏高）'
        if pct < 0.3:   return f'个别资源池（{pct*100:.0f}%）'
        return f'混合（{pct*100:.0f}%）'

    def _region_cause_str(r):
        if r not in region_root_cause:
            return '-'
        rc_name, rc_rel, az1_age, oth_age = region_root_cause[r]
        if rc_name == '服务器折旧':
            return f'服务器折旧偏高（az1均龄 {az1_age:.1f}年 vs 其他 {oth_age:.1f}年）'
        return f'{rc_name}偏高 {rc_rel*100:.0f}%'

    dc_prem_rows = ''.join(
        "<tr>"
        f"<td>{r}</td>"
        f"<td style='color:#DC2626;font-weight:600'>+{az1_total_premium.get(r,0):.1f}%</td>"
        f"<td style='color:#DC2626;font-weight:600'>+{az1_dc_premium.get(r,0):.1f}%</td>"
        f"<td>{_sys_tag(r)}</td>"
        f"<td>{_region_cause_str(r)}</td>"
        f"<td>{_badge(az1_dc_premium.get(r, 0))}</td>"
        "</tr>"
        for r in focus_regions if r in region_root_cause
    )

    # ── 汇总表2：region+资源类型（仅个别/混合region，max 3行，含根因） ─────────
    conc_regions = [r for r in focus_regions if region_sys.get(r, 0) <= 0.7]
    if conc_regions:
        detail = (focus_rtype[focus_rtype['region'].isin(conc_regions) & focus_rtype['sig']]
                  .sort_values('gap_pct', ascending=False)
                  .head(3))
        if len(detail) > 0:
            detail_rows = ''
            for _, row in detail.iterrows():
                gap_val = row['gap_pct'] * 100
                color = '#c0392b' if gap_val > 20 else '#e67e22'
                if row['cause'] == '服务器折旧':
                    cause_str = (f"服务器折旧偏高"
                                 f"（az1均龄 {row['az1_age']:.1f}年 vs 其他 {row['oth_age']:.1f}年）")
                elif row['cause']:
                    cause_str = f"{row['cause']}偏高 {row['cause_gap']*100:.0f}%"
                else:
                    cause_str = '-'
                detail_rows += (
                    f"<tr><td>{row['region']}</td>"
                    f"<td>{row['resource_type']}</td>"
                    f"<td>{row['az1_uc']:.2f}</td>"
                    f"<td>{row['oth_uc']:.2f}</td>"
                    f"<td style='color:{color};font-weight:bold'>+{gap_val:.1f}%</td>"
                    f"<td>{cause_str}</td></tr>"
                )
            rtype_gap_table = (
                "<table><thead><tr>"
                "<th>Region</th><th>资源类型</th><th>az1单台成本</th>"
                "<th>其他AZ均值</th><th>az1溢价</th><th>成本根因</th>"
                f"</tr></thead><tbody>{detail_rows}</tbody></table>"
            )
        else:
            rtype_gap_table = ""
    else:
        # 全部系统性，无需资源类型明细
        rtype_gap_table = ""

    return {
        'chart':            chart_b64,
        'chart1b':          chart1b_b64,
        'dc_prem_rows':     dc_prem_rows,
        'rtype_gap_table':  rtype_gap_table,
        'az1_insight':      az1_insight,
        'diff_insight':     diff_insight,
        'migration_hint':   migration_hint,
        'focus_regions':    focus_regions,
    }
