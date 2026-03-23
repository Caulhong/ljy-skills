"""
云资源成本经营分析报告 — 编排脚本
用法：python3 generate_report.py [--output /path/to/report.html]

分析模块：
  analysis1_az_cost.py       — AZ 成本差异 + 非算力拆解 + 阶段分布
  analysis2_problem_pools.py — 全阶段经营诊断（起步/主力售卖/存量经营/退出整合）
  analysis3_oversell.py      — 超卖空间识别（前三阶段大资源池）

模板：templates/report_template.html
"""
import sys, os, warnings
import pymysql
import pandas as pd

warnings.filterwarnings('ignore')

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, '..', 'templates', 'report_template.html')
OUTPUT_PATH   = '/tmp/cloud_cost_report.html'
for i, arg in enumerate(sys.argv):
    if arg == '--output' and i + 1 < len(sys.argv):
        OUTPUT_PATH = sys.argv[i + 1]

sys.path.insert(0, SCRIPT_DIR)
import analysis1_az_cost
import analysis2_problem_pools
import analysis3_oversell

# ── 数据加载 ──────────────────────────────────────────────────────────────
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='dqh12345', database='hw1')
df = pd.read_sql("SELECT * FROM cloud_cost", conn)
conn.close()

latest_month = df['month'].max()
dfc = df[df['month'] == latest_month].copy()
# 计算单台成本/流水（供分析模块使用）
dfc['unit_cost']    = dfc['cost']    / dfc['server_count']
dfc['unit_revenue'] = dfc['revenue'] / dfc['server_count']

# ── 运行三个分析模块（全部传入全阶段数据） ────────────────────────────────
r1 = analysis1_az_cost.run(dfc)
r2 = analysis2_problem_pools.run(dfc)
r3 = analysis3_oversell.run(dfc)

# ── KPI 汇总 ──────────────────────────────────────────────────────────────
total_pools    = len(dfc)
avg_gm         = dfc['gross_margin'].mean() * 100
prob_count     = r2['prob_count']
decom_count    = r2['decom_count']
oversell_count = r3['oversell_count']

# ── 综合建议行 ────────────────────────────────────────────────────────────
summary_rows = (
    f"<tr><td><span class='tag r'>P0</span></td><td>主力售卖 az1 DC成本溢价</td>"
    f"<td>溢价&gt;20% 的 az1</td><td>新售切 az2/az3；存量续约迁移</td></tr>"

    f"<tr><td><span class='tag r'>P0</span></td><td>主力售卖高台数-低毛利</td>"
    f"<td>{prob_count} 个异常池</td><td>排查销售策略 / 评估AZ迁移</td></tr>"

    + (f"<tr><td><span class='tag r'>P0</span></td><td>退出整合持续亏损</td>"
       f"<td>{decom_count} 个资源池</td><td>评估下线或资源整合</td></tr>"
       if decom_count > 0 else "")

    + f"<tr><td><span class='tag y'>P1</span></td><td>存量经营亏损池</td>"
    f"<td>见分析2</td><td>排查定价/分配率；提升流水</td></tr>"

    f"<tr><td><span class='tag y'>P1</span></td><td>超卖空间</td>"
    f"<td>{oversell_count} 个大资源池</td><td>超卖比+监控预警（分阶段试点）</td></tr>"

    f"<tr><td><span class='tag gr'>P2</span></td><td>起步低分配率</td>"
    f"<td>见分析2</td><td>加强拉新；评估资源规划</td></tr>"
)

# ── 填充模板 ──────────────────────────────────────────────────────────────
with open(TEMPLATE_PATH, encoding='utf-8') as f:
    html = f.read()

replacements = {
    '__LATEST_MONTH__':     str(latest_month),
    '__TOTAL_POOLS__':      str(total_pools),
    '__AVG_GM__':           f'{avg_gm:.1f}%',
    '__PROB_COUNT__':       str(prob_count),
    '__DECOM_COUNT__':      str(decom_count),
    '__OVERSELL_COUNT__':   str(oversell_count),
    # 分析1
    '__AZ1_INSIGHT__':      r1['az1_insight'],
    '__CHART1__':           r1['chart'],
    '__CHART1B__':          r1['chart1b'],
    '__DC_PREM_ROWS__':     r1['dc_prem_rows'],
    '__RTYPE_GAP_TABLE__':  r1['rtype_gap_table'],
    '__DIFF_INSIGHT__':     r1['diff_insight'],
    '__MIGRATION_HINT__':   r1['migration_hint'],
    # 分析2
    '__CHART2_MS__':        r2['chart_ms'],
    '__CHART2_OTHER__':     r2['chart_other'],
    '__TABLE_MS__':         r2['table_ms'],
    '__TABLE_QIBU__':       r2['table_qibu'],
    '__TABLE_CUNLIANG__':   r2['table_cunliang'],
    '__TABLE_QIECHU__':     r2['table_qiechu'],
    '__INSIGHT_QIBU__':     r2['insight_qibu'],
    '__INSIGHT_MS__':       r2['insight_ms'],
    '__INSIGHT_CUNLIANG__': r2['insight_cunliang'],
    '__INSIGHT_QIECHU__':   r2['insight_qiechu'],
    # 分析3
    '__CHART3__':           r3['chart'],
    '__OVERSELL_TABLE__':   r3['table'],
    '__OVERSELL_INSIGHT__': r3['oversell_insight'],
    # 综合
    '__SUMMARY_ROWS__':     summary_rows,
}

for key, val in replacements.items():
    html = html.replace(key, val)

with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"报告已生成：{OUTPUT_PATH}")
