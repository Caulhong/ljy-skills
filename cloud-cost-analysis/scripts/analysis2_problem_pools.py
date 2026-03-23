"""
分析2：按阶段分类的资源池经营诊断

- 起步：亏损正常，重点看分配率和CPU使用率
- 主力售卖：高台数-低毛利四象限诊断
- 存量经营：折旧归零后是否仍盈利
- 退出整合：不盈利则建议下线

输入：df DataFrame（所有阶段数据）
输出：dict {
    'chart_ms': 主力售卖四象限 base64,
    'chart_other': 其他三阶段散点 base64,
    'table_ms': 主力售卖问题池 HTML,
    'table_qibu': 起步低利用率池 HTML,
    'table_cunliang': 存量经营亏损池 HTML,
    'table_qiechu': 退出整合下线建议 HTML,
    'insight_qibu': 起步结论文字,
    'insight_ms': 主力售卖结论文字,
    'insight_cunliang': 存量经营结论文字,
    'insight_qiechu': 退出整合结论文字,
    'prob_count': 主力售卖异常池数量,
    'decom_count': 退出整合建议下线数量,
    '_ms_with_quad': 主力售卖带象限标签的 DataFrame,
}
"""
import io, base64, json, math
import numpy as np
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams, font_manager

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'assets', 'fonts', 'NotoSansSC-Regular.otf')
if os.path.exists(_FONT_PATH):
    font_manager.fontManager.addfont(_FONT_PATH)

rcParams['font.sans-serif'] = ['Noto Sans SC', 'Microsoft YaHei', 'SimHei',
                                'Hiragino Sans GB', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def _pool_label(row):
    return f"{row['region'][-3:]} {row['az']}/{row['resource_type']}"


# 台数分组定义
_GROUPS  = ['1-100', '100-500', '500-1000', '>1000']
_COLORS  = ['#CBD5E1', '#38BDF8', '#34D399', '#FBBF24']
_CHECKED = [False, True, True, True]   # 1-100 默认不勾选


def _count_group(n):
    if n <= 100:  return '1-100'
    if n <= 500:  return '100-500'
    if n <= 1000: return '500-1000'
    return '>1000'


def _bubble_r(count, max_count):
    """
    服务器台数 → 气泡半径（px），全局连续 sqrt 缩放。
    台数越多半径越大，同组内不同台数大小也有差异。
    """
    if max_count <= 0:
        return 5.0
    ratio = count / max_count
    return round(max(2.0, min(8.0, 2.0 + 6.0 * math.sqrt(ratio))), 1)


def _ms_scatter_html(df):
    """
    主力售卖期：分配率 vs 毛利率 交互式气泡图（Chart.js）。
    - X: 分配率 0-110%
    - Y: 毛利率 -200% to 100%
    - 气泡大小: 服务器台数
    - 过滤异常：毛利率 < -180% 或 > 85% 视为异常数据，不展示
    """
    valid = df[
        (df['gross_margin'] >= -1.80) &
        (df['gross_margin'] <= 0.85) &
        (df['allocation_rate'] >= 0) &
        (df['allocation_rate'] <= 1.10)
    ].copy()
    valid['_group'] = valid['server_count'].apply(_count_group)

    # 全局最大台数，保证跨分组气泡大小可比
    max_count = int(valid['server_count'].max()) if len(valid) > 0 else 1

    datasets = []
    for g, color, checked in zip(_GROUPS, _COLORS, _CHECKED):
        grp = valid[valid['_group'] == g]
        pts = [
            {
                'x': round(float(r['allocation_rate']) * 100, 1),
                'y': round(float(r['gross_margin'])    * 100, 1),
                'r': _bubble_r(int(r['server_count']), max_count),
                'count': int(r['server_count']),
                'pool':  _pool_label(r),
            }
            for _, r in grp.iterrows()
        ]
        datasets.append({
            'label':           g + '台',
            'data':            pts,
            'backgroundColor': color + 'BB',
            'borderColor':     color,
            'borderWidth':     1,
            'hidden':          not checked,
        })

    ds_json = json.dumps(datasets, ensure_ascii=False)

    # 过滤掉的异常点数量
    outliers = len(df) - len(valid)
    outlier_note = (f'<span style="color:var(--text3);font-size:12px;margin-left:8px">'
                    f'已过滤 {outliers} 个异常点（毛利率&lt;-180% 或 &gt;85%）</span>'
                    if outliers > 0 else '')

    # 图例 + checkbox 控件
    cb_html = ''.join(
        f'<label class="scatter-cb{" " if c else " unchecked"}" id="cb-ms-{i}" '
        f'onclick="toggleMs({i},this)">'
        f'<span class="dot" style="background:{col}"></span>{g}台</label>'
        for i, (g, col, c) in enumerate(zip(_GROUPS, _COLORS, _CHECKED))
    )

    font_family = "'Noto Sans SC', 'Microsoft YaHei', sans-serif"
    return f"""<div class="scatter-wrap">
  <div class="scatter-filters">
    <span>台数筛选：</span>{cb_html}{outlier_note}
  </div>
  <div style="position:relative;height:440px">
    <canvas id="scatterMs"></canvas>
  </div>
</div>
<script>
(function(){{
  var ds = {ds_json};
  var ctx = document.getElementById('scatterMs').getContext('2d');
  var msChart = new Chart(ctx, {{
    type: 'bubble',
    data: {{ datasets: ds }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: {{ duration: 200 }},
      scales: {{
        x: {{
          title: {{ display: true, text: '分配率（%）', font: {{ size: 12, family: "{font_family}" }} }},
          min: 0, max: 110,
          grid: {{ color: '#E2E8F0' }},
          ticks: {{ font: {{ family: "{font_family}" }} }}
        }},
        y: {{
          title: {{ display: true, text: '毛利率（%）', font: {{ size: 12, family: "{font_family}" }} }},
          min: -200, max: 100,
          grid: {{ color: '#E2E8F0' }},
          ticks: {{ font: {{ family: "{font_family}" }} }}
        }}
      }},
      onClick: function(e, elements, chart) {{
        if (elements.length > 0) {{
          var el = elements[0];
          var pt = chart.data.datasets[el.datasetIndex].data[el.index];
          window.highlightMs(pt.pool);
        }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: function(c) {{
              var d = c.raw;
              return [d.pool, '分配率: ' + d.x + '%', '毛利率: ' + d.y + '%', '台数: ' + d.count];
            }}
          }},
          titleFont: {{ family: "{font_family}" }},
          bodyFont:  {{ family: "{font_family}" }}
        }}
      }}
    }}
  }});

  // 保存各 dataset 原始单色（后续高亮时按点覆盖）
  msChart.data.datasets.forEach(function(d) {{ d._bc = d.borderColor; }});

  window.toggleMs = function(idx, lbl) {{
    msChart.data.datasets[idx].hidden = !msChart.data.datasets[idx].hidden;
    lbl.classList.toggle('unchecked');
    msChart.update();
  }};

  // 高亮指定资源池：红色描边 + 自动弹 tooltip + 表格行变色
  window.highlightMs = function(poolLabel) {{
    document.querySelectorAll('.ms-row').forEach(function(r) {{ r.classList.remove('ms-row-on'); }});
    var activeRow = document.querySelector('.ms-row[data-pool="' + poolLabel + '"]');
    if (activeRow) {{
      activeRow.classList.add('ms-row-on');
      activeRow.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
    }}

    var foundDs = -1, foundPt = -1;
    msChart.data.datasets.forEach(function(ds, di) {{
      ds.data.forEach(function(pt, pi) {{
        if (pt.pool === poolLabel) {{ foundDs = di; foundPt = pi; }}
      }});
    }});
    if (foundDs < 0) return;

    // 如果所在分组被隐藏，自动展示
    if (msChart.data.datasets[foundDs].hidden) {{
      msChart.data.datasets[foundDs].hidden = false;
      var cb = document.getElementById('cb-ms-' + foundDs);
      if (cb) cb.classList.remove('unchecked');
    }}

    // 重置所有点描边为原色，再将目标点改为红色粗边
    msChart.data.datasets.forEach(function(ds) {{
      ds.borderColor = ds.data.map(function() {{ return ds._bc; }});
      ds.borderWidth = ds.data.map(function() {{ return 1; }});
    }});
    msChart.data.datasets[foundDs].borderColor[foundPt] = '#DC2626';
    msChart.data.datasets[foundDs].borderWidth[foundPt] = 3;

    // 自动显示 tooltip
    var meta = msChart.getDatasetMeta(foundDs);
    msChart.tooltip.setActiveElements(
      [{{datasetIndex: foundDs, index: foundPt}}],
      {{x: meta.data[foundPt].x, y: meta.data[foundPt].y}}
    );
    msChart.update();
  }};

  window.clearMsHighlight = function() {{
    document.querySelectorAll('.ms-row').forEach(function(r) {{ r.classList.remove('ms-row-on'); }});
    msChart.data.datasets.forEach(function(ds) {{ ds.borderColor = ds._bc; ds.borderWidth = 1; }});
    msChart.tooltip.setActiveElements([], {{x:0,y:0}});
    msChart.update();
  }};

  // 点击图表空白区域时清除高亮
  document.getElementById('scatterMs').addEventListener('click', function(e) {{
    var pts = msChart.getElementsAtEventForMode(e, 'nearest', {{intersect: true}}, false);
    if (pts.length === 0) clearMsHighlight();
  }});
}})();
</script>"""


def run(df):
    """
    df: 所有阶段 DataFrame，需包含列：
        region, az, resource_type, stage, server_count, gross_margin,
        unit_cost, unit_revenue, allocation_rate, cpu_usage, cost
    返回 dict。
    """
    df = df.copy()

    qibu     = df[df['stage'] == '起步'].copy()
    ms       = df[df['stage'] == '主力售卖'].copy()
    cunliang = df[df['stage'] == '存量经营'].copy()
    qiechu   = df[df['stage'] == '退出整合'].copy()

    # ══════════════════════════════════════════════════════════════════════════
    # Chart A：主力售卖 — 分配率 vs 毛利率 交互气泡图（Chart.js）
    # ══════════════════════════════════════════════════════════════════════════
    if len(ms) > 0:
        srv_med = ms['server_count'].median()
        gm_med  = ms['gross_margin'].median()
        ms = ms.copy()
        ms['quad'] = '低台数-低毛利'
        ms.loc[(ms['server_count'] >= srv_med) & (ms['gross_margin'] >= gm_med), 'quad'] = '高台数-高毛利'
        ms.loc[(ms['server_count'] >= srv_med) & (ms['gross_margin'] <  gm_med), 'quad'] = '高台数-低毛利⚠'
        ms.loc[(ms['server_count'] <  srv_med) & (ms['gross_margin'] >= gm_med), 'quad'] = '低台数-高毛利'
        chart_ms_html = _ms_scatter_html(ms)
    else:
        chart_ms_html = '<p>暂无主力售卖期数据。</p>'

    # ══════════════════════════════════════════════════════════════════════════
    # Chart B：其他三阶段（起步 / 存量经营 / 退出整合）
    # ══════════════════════════════════════════════════════════════════════════
    fig_other, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig_other.suptitle('分析二B：其他阶段资源池经营状况', fontsize=12, fontweight='bold')

    # ── 起步：分配率 vs CPU 散点 ────────────────────────────────────────────
    ax_qb = axes[0]
    if len(qibu) > 0:
        low_alloc = qibu[qibu['allocation_rate'] < 0.3]
        normal_qb = qibu[qibu['allocation_rate'] >= 0.3]
        ax_qb.scatter(normal_qb['allocation_rate'] * 100, normal_qb['cpu_usage'] * 100,
                      c='#3498DB', alpha=0.65, s=50, label='正常')
        ax_qb.scatter(low_alloc['allocation_rate'] * 100, low_alloc['cpu_usage'] * 100,
                      c='#E74C3C', alpha=0.85, s=60, label=f'低分配率（{len(low_alloc)}个）')
        ax_qb.axvline(30, color='#E74C3C', linestyle='--', linewidth=1, alpha=0.5)
        ax_qb.set_xlabel('分配率（%）', fontsize=9)
        ax_qb.set_ylabel('CPU 使用率（%）', fontsize=9)
        ax_qb.legend(fontsize=8)
    ax_qb.set_title('起步阶段\n（亏损正常，关注利用率）', fontsize=10, fontweight='bold')
    ax_qb.grid(True, linestyle='--', alpha=0.4)
    ax_qb.set_axisbelow(True)

    # ── 存量经营：台数 vs 毛利率 ────────────────────────────────────────────
    ax_cl = axes[1]
    if len(cunliang) > 0:
        pos_cl = cunliang[cunliang['gross_margin'] >= 0]
        neg_cl = cunliang[cunliang['gross_margin'] < 0]
        ax_cl.scatter(pos_cl['server_count'], pos_cl['gross_margin'] * 100,
                      c='#27AE60', alpha=0.65, s=50, label='盈利')
        ax_cl.scatter(neg_cl['server_count'], neg_cl['gross_margin'] * 100,
                      c='#E74C3C', alpha=0.85, s=60, label=f'亏损（{len(neg_cl)}个）⚠')
        ax_cl.axhline(0, color='#E74C3C', linestyle='--', linewidth=1, alpha=0.6)
        ax_cl.set_xlabel('服务器台数', fontsize=9)
        ax_cl.set_ylabel('毛利率（%）', fontsize=9)
        ax_cl.legend(fontsize=8)
    ax_cl.set_title('存量经营阶段\n（折旧归零，是否仍盈利）', fontsize=10, fontweight='bold')
    ax_cl.grid(True, linestyle='--', alpha=0.4)
    ax_cl.set_axisbelow(True)

    # ── 退出整合：台数 vs 毛利率，亏损标注 ─────────────────────────────────
    ax_qc = axes[2]
    if len(qiechu) > 0:
        pos_qc = qiechu[qiechu['gross_margin'] >= 0]
        neg_qc = qiechu[qiechu['gross_margin'] < 0]
        ax_qc.scatter(pos_qc['server_count'], pos_qc['gross_margin'] * 100,
                      c='#BDC3C7', alpha=0.65, s=50, label='尚可运营')
        ax_qc.scatter(neg_qc['server_count'], neg_qc['gross_margin'] * 100,
                      c='#C0392B', alpha=0.9, s=65, marker='x', label=f'建议下线（{len(neg_qc)}个）')
        ax_qc.axhline(0, color='#C0392B', linestyle='--', linewidth=1, alpha=0.6)
        # 标注最差3个
        for _, row in neg_qc.nsmallest(3, 'gross_margin').iterrows():
            ax_qc.annotate(_pool_label(row), (row['server_count'], row['gross_margin'] * 100),
                           textcoords='offset points', xytext=(5, -12),
                           fontsize=7, color='#C0392B',
                           arrowprops=dict(arrowstyle='->', color='#C0392B', lw=0.7))
        ax_qc.set_xlabel('服务器台数', fontsize=9)
        ax_qc.set_ylabel('毛利率（%）', fontsize=9)
        ax_qc.legend(fontsize=8)
    ax_qc.set_title('退出整合阶段\n（亏损池建议下线）', fontsize=10, fontweight='bold')
    ax_qc.grid(True, linestyle='--', alpha=0.4)
    ax_qc.set_axisbelow(True)

    plt.tight_layout()
    chart_other_b64 = _fig_to_b64(fig_other)

    # ══════════════════════════════════════════════════════════════════════════
    # 根因分析 & 表格（主力售卖）
    # ══════════════════════════════════════════════════════════════════════════
    problem_pools = ms[ms['quad'] == '高台数-低毛利⚠'].copy() if len(ms) > 0 else ms
    if len(problem_pools) > 0 and len(ms) > 0:
        type_med = ms.groupby('resource_type').agg(
            med_uc=('unit_cost',      'median'),
            med_ur=('unit_revenue',   'median'),
            med_al=('allocation_rate','median'),
        ).reset_index()
        problem_pools = problem_pools.merge(type_med, on='resource_type')
        problem_pools['cost_gap']  = (problem_pools['unit_cost']      - problem_pools['med_uc']) / problem_pools['med_uc']
        problem_pools['alloc_gap'] = (problem_pools['allocation_rate'] - problem_pools['med_al']) / problem_pools['med_al']
        top_problem = problem_pools.nsmallest(6, 'gross_margin')

        cost_high  = (top_problem['cost_gap']  >  0.10).sum()
        alloc_low  = (top_problem['alloc_gap'] < -0.15).sum()
        prob_count = len(problem_pools)

        if cost_high > alloc_low:
            insight_ms = f"异常池中以<strong>成本偏高型</strong>为主（{cost_high} 个），单台成本超同类中位 10% 以上。"
        elif alloc_low > cost_high:
            insight_ms = f"异常池中以<strong>流水偏低型</strong>为主（{alloc_low} 个），分配率低于同类中位 15% 以上，大量服务器空置未售。"
        elif cost_high == 0 and alloc_low == 0:
            insight_ms = "高台数资源池整体毛利表现良好，当前无需重点干预。"
        else:
            insight_ms = f"成本偏高型（{cost_high} 个）与流水偏低型（{alloc_low} 个）并存，需分类处理。"

        rows_ms = ''
        for _, r in top_problem.iterrows():
            causes = []
            if r['cost_gap']  >  0.10: causes.append(f"成本偏高 {r['cost_gap']*100:+.0f}%")
            if r['alloc_gap'] < -0.15: causes.append(f"分配率低 {r['allocation_rate']*100:.0f}%")
            pool = _pool_label(r)
            rows_ms += (
                f"<tr class='ms-row' data-pool='{pool}' onclick='highlightMs(\"{pool}\")'>"
                f"<td>{pool}</td>"
                f"<td>{int(r['server_count'])}</td>"
                f"<td style='color:#e74c3c;font-weight:bold'>{r['gross_margin']*100:.1f}%</td>"
                f"<td>{r['allocation_rate']*100:.0f}%</td>"
                f"<td style='color:#c0392b'>{'；'.join(causes) or '—'}</td></tr>"
            )
        table_ms = (
            "<table id='tbl-ms'><thead><tr>"
            "<th>资源池</th><th>服务器台数</th><th>毛利率</th><th>分配率</th><th>主要原因</th>"
            f"</tr></thead><tbody>{rows_ms}</tbody></table>"
        )
    else:
        prob_count = 0
        insight_ms = "主力售卖期高台数资源池毛利表现良好。"
        table_ms   = "<p>暂无异常资源池。</p>"
        top_problem = ms

    # ══════════════════════════════════════════════════════════════════════════
    # 起步：表格（低分配率 top 6）
    # ══════════════════════════════════════════════════════════════════════════
    if len(qibu) > 0:
        low_alloc_count = (qibu['allocation_rate'] < 0.3).sum()
        avg_alloc_qb = qibu['allocation_rate'].mean()
        avg_cpu_qb   = qibu['cpu_usage'].mean()
        insight_qibu = (
            f"起步阶段共 <strong>{len(qibu)}</strong> 个资源池，亏损属正常现象，关键看资源能否售出。"
            f"分配率均值 <strong>{avg_alloc_qb*100:.1f}%</strong>，CPU 均值 <strong>{avg_cpu_qb*100:.1f}%</strong>。"
            + (f"其中 <strong>{low_alloc_count}</strong> 个池分配率低于 30%，拉新进展需关注。"
               if low_alloc_count > 0 else "整体分配率达标，拉新节奏正常。")
        )
        top_qb = qibu.nlargest(6, 'server_count')
        rows_qb = ''
        for _, r in top_qb.iterrows():
            alloc_style = "style='color:#e74c3c;font-weight:bold'" if r['allocation_rate'] < 0.3 else ''
            rows_qb += (
                f"<tr><td>{_pool_label(r)}</td>"
                f"<td>{int(r['server_count'])}</td>"
                f"<td {alloc_style}>{r['allocation_rate']*100:.0f}%</td>"
                f"<td>{r['cpu_usage']*100:.0f}%</td>"
                f"<td style='color:#e74c3c'>{r['gross_margin']*100:.1f}%（预期亏损）</td></tr>"
            )
        table_qibu = (
            "<table><thead><tr>"
            "<th>资源池</th><th>台数</th><th>分配率</th><th>CPU使用率</th><th>毛利率</th>"
            f"</tr></thead><tbody>{rows_qb}</tbody></table>"
        )
    else:
        insight_qibu = "当前无起步阶段资源池。"
        table_qibu   = "<p>无数据。</p>"

    # ══════════════════════════════════════════════════════════════════════════
    # 存量经营：表格（亏损池）
    # ══════════════════════════════════════════════════════════════════════════
    if len(cunliang) > 0:
        neg_cl = cunliang[cunliang['gross_margin'] < 0]
        avg_gm_cl = cunliang['gross_margin'].mean()
        if len(neg_cl) > 0:
            insight_cunliang = (
                f"存量经营阶段 <strong>{len(cunliang)}</strong> 个资源池中，"
                f"<strong style='color:#e74c3c'>{len(neg_cl)}</strong> 个毛利为负——"
                f"折旧已归零，非算力成本仍高于流水，需排查分配率或定价问题。"
                f"整体平均毛利率 <strong>{avg_gm_cl*100:.1f}%</strong>。"
            )
            show_cl = neg_cl.nsmallest(6, 'gross_margin')
        else:
            insight_cunliang = (
                f"存量经营阶段 <strong>{len(cunliang)}</strong> 个资源池整体盈利，"
                f"平均毛利率 <strong>{avg_gm_cl*100:.1f}%</strong>，无需重点干预。"
            )
            show_cl = cunliang.nsmallest(6, 'gross_margin')

        rows_cl = ''.join(
            f"<tr><td>{_pool_label(r)}</td>"
            f"<td>{int(r['server_count'])}</td>"
            f"<td style='color:{'#e74c3c' if r['gross_margin'] < 0 else '#27ae60'};font-weight:bold'>"
            f"{r['gross_margin']*100:.1f}%</td>"
            f"<td>{r['allocation_rate']*100:.0f}%</td>"
            f"<td>{'⚠ 排查定价/分配率' if r['gross_margin'] < 0 else '—'}</td></tr>"
            for _, r in show_cl.iterrows()
        )
        table_cunliang = (
            "<table><thead><tr>"
            "<th>资源池</th><th>台数</th><th>毛利率</th><th>分配率</th><th>建议</th>"
            f"</tr></thead><tbody>{rows_cl}</tbody></table>"
        )
    else:
        insight_cunliang = "当前无存量经营阶段资源池。"
        table_cunliang   = "<p>无数据。</p>"

    # ══════════════════════════════════════════════════════════════════════════
    # 退出整合：表格（亏损→建议下线）
    # ══════════════════════════════════════════════════════════════════════════
    if len(qiechu) > 0:
        neg_qc = qiechu[qiechu['gross_margin'] < 0]
        decom_count = len(neg_qc)
        if decom_count > 0:
            insight_qiechu = (
                f"退出整合阶段 <strong>{len(qiechu)}</strong> 个资源池中，"
                f"<strong style='color:#c0392b'>{decom_count}</strong> 个持续亏损。"
                f"折旧归零后仍无法覆盖运营成本，建议评估下线或资源整合。"
            )
        else:
            insight_qiechu = (
                f"退出整合阶段 <strong>{len(qiechu)}</strong> 个资源池当前均盈利，"
                f"可继续运营，关注流水是否稳定。"
            )

        show_qc = qiechu.sort_values('gross_margin').head(8)
        rows_qc = ''.join(
            f"<tr><td>{_pool_label(r)}</td>"
            f"<td>{int(r['server_count'])}</td>"
            f"<td style='color:{'#c0392b' if r['gross_margin'] < 0 else '#27ae60'};font-weight:bold'>"
            f"{r['gross_margin']*100:.1f}%</td>"
            f"<td>{r['allocation_rate']*100:.0f}%</td>"
            f"<td style='color:#c0392b;font-weight:bold'>{'🔴 建议下线' if r['gross_margin'] < 0 else '✅ 可继续运营'}</td></tr>"
            for _, r in show_qc.iterrows()
        )
        table_qiechu = (
            "<table><thead><tr>"
            "<th>资源池</th><th>台数</th><th>毛利率</th><th>分配率</th><th>建议</th>"
            f"</tr></thead><tbody>{rows_qc}</tbody></table>"
        )
    else:
        decom_count    = 0
        insight_qiechu = "当前无退出整合阶段资源池。"
        table_qiechu   = "<p>无数据。</p>"

    return {
        'chart_ms':        chart_ms_html,
        'chart_other':     chart_other_b64,
        'table_ms':        table_ms,
        'table_qibu':      table_qibu,
        'table_cunliang':  table_cunliang,
        'table_qiechu':    table_qiechu,
        'insight_qibu':    insight_qibu,
        'insight_ms':      insight_ms,
        'insight_cunliang': insight_cunliang,
        'insight_qiechu':  insight_qiechu,
        'prob_count':      prob_count,
        'decom_count':     decom_count,
        '_ms_with_quad':   ms,
    }
