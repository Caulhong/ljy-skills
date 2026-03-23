"""
重建 cloud_cost 数据，使其符合基本业务规律。

成本模型：
  - 算力成本 = 服务器折旧成本 = 单台月折旧 × 台数（固定，不随AZ变化，4年/48月）
  - 非算力成本 = dc成本 + dcn成本 + 网络设备成本
    - dc成本：最大非算力项，随AZ有明显差异（az1偏贵，供分析1使用）
    - dcn成本、网络设备成本：相对稳定，AZ间差异小
  - 总成本 = 算力 + 非算力
  - 流水 = 单台售价 × 台数 × 分配率（售价约为总单台成本 2.4x，保证正毛利）
  - 毛利 = (流水 - 成本×1.18) / 流水
"""
import pymysql, random
random.seed(2025)

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='dqh12345', database='hw1')
cur = conn.cursor()

regions = [
    '华北-北京一', '华北-北京二',
    '华东-上海一', '华东-上海二',
    '华南-广州一', '华南-深圳一',
    '西南-成都一', '西北-西安一',
    '东北-沈阳一', '华中-武汉一'
]
azs = ['az1', 'az2', 'az3']

# 单台月成本基准（万元/台/月），4年折旧固定
# (资源类型, 月折旧, dc基准, dcn基准, network基准, 单台售价)
# 成本占比设计：折旧~42%, dc~35%, dcn~13%, network~10%
# 售价 ≈ 总单台成本 × 2.4，保证65%分配率下毛利约22%
resource_specs = {
    'c7-2':  dict(dep=0.25, dc=0.21, dcn=0.08, net=0.06, price=1.44),
    'c7-4':  dict(dep=0.45, dc=0.37, dcn=0.14, net=0.11, price=2.58),
    'c7-8':  dict(dep=0.85, dc=0.71, dcn=0.26, net=0.20, price=4.86),
    'c7-16': dict(dep=1.50, dc=1.25, dcn=0.46, net=0.36, price=8.58),
    'm7-2':  dict(dep=0.32, dc=0.27, dcn=0.10, net=0.08, price=1.85),
    'm7-4':  dict(dep=0.58, dc=0.48, dcn=0.18, net=0.14, price=3.31),
    'm7-8':  dict(dep=1.05, dc=0.87, dcn=0.33, net=0.25, price=6.00),
    'g7-2':  dict(dep=2.20, dc=1.83, dcn=0.68, net=0.52, price=12.59),
    'r7-4':  dict(dep=0.72, dc=0.60, dcn=0.22, net=0.17, price=4.12),
    's7-2':  dict(dep=0.20, dc=0.17, dcn=0.06, net=0.05, price=1.15),
}
months = [202601, 202602]

# DC成本的AZ系数：az1在部分region明显偏贵（系统性，所有资源类型受影响）
# 华东-上海一 az1 DC成本与其他AZ基本持平，仅少数资源类型例外（个别问题）
az_dc_factor = {
    '华北-北京一': {'az1': 1.30, 'az2': 1.00, 'az3': 1.02},
    '华北-北京二': {'az1': 1.06, 'az2': 1.00, 'az3': 1.03},
    '华东-上海一': {'az1': 1.02, 'az2': 1.01, 'az3': 1.00},   # 无系统性az1溢价
    '华东-上海二': {'az1': 1.05, 'az2': 1.00, 'az3': 1.02},
    '华南-广州一': {'az1': 1.28, 'az2': 1.00, 'az3': 1.01},
    '华南-深圳一': {'az1': 1.07, 'az2': 1.02, 'az3': 1.00},
    '西南-成都一': {'az1': 1.25, 'az2': 1.00, 'az3': 1.03},
    '西北-西安一': {'az1': 1.04, 'az2': 1.00, 'az3': 1.01},
    '东北-沈阳一': {'az1': 1.20, 'az2': 1.00, 'az3': 1.02},
    '华中-武汉一': {'az1': 1.08, 'az2': 1.00, 'az3': 1.04},
}

# 特定资源类型的DC成本额外系数（个别资源池问题）
# 华东-上海一 仅 c7-8 和 g7-2 的 az1 DC成本显著偏高
rtype_dc_override = {
    ('华东-上海一', 'az1', 'c7-8'):  1.45,
    ('华东-上海一', 'az1', 'g7-2'):  1.38,
}
# DCN 和 网络设备 的AZ系数（差异小）
az_dcn_factor = {r: {'az1': 1.04, 'az2': 1.00, 'az3': 1.01} for r in regions}
az_net_factor = {r: {'az1': 1.02, 'az2': 1.00, 'az3': 1.01} for r in regions}

# 特殊资源池标记（同上版本）
low_alloc_pools = {
    ('华东-上海一', 'az2', 'c7-8'),
    ('华东-上海一', 'az3', 'c7-16'),
    ('华南-广州一', 'az1', 'm7-4'),
    ('西南-成都一', 'az2', 'g7-2'),
}
high_cost_pools = {
    ('华北-北京一', 'az1', 'c7-8'),
    ('华东-上海一', 'az1', 'c7-16'),
    ('华南-广州一', 'az1', 'c7-4'),
}
oversell_pools = {
    ('华北-北京二', 'az2', 'c7-4'),
    ('华东-上海二', 'az1', 'c7-8'),
    ('华南-深圳一', 'az3', 'm7-2'),
    ('西北-西安一', 'az2', 'c7-2'),
}

stage_weights = ['起步']*10 + ['主力售卖']*60 + ['存量经营']*20 + ['退出整合']*10

def gen_age(stage):
    return {
        '起步':    round(random.uniform(0.1, 0.9), 1),
        '主力售卖': round(random.uniform(1.0, 4.9), 1),
        '存量经营': round(random.uniform(5.0, 7.0), 1),
        '退出整合': round(random.uniform(7.1, 12.0), 1),
    }[stage]

cur.execute("DELETE FROM cloud_cost")
conn.commit()

rows = []
for month in months:
    for region in regions:
        for az in azs:
            for rtype, spec in resource_specs.items():
                stage = random.choice(stage_weights)
                age = gen_age(stage)

                # 服务器台数
                if stage == '主力售卖':
                    server_count = random.randint(800, 2500)
                elif stage == '存量经营':
                    server_count = random.randint(300, 900)
                elif stage == '退出整合':
                    server_count = random.randint(10, 300)   # 含极小台数
                else:
                    server_count = random.randint(100, 400)

                if (region, az, rtype) in low_alloc_pools or (region, az, rtype) in high_cost_pools:
                    server_count = max(server_count, random.randint(1600, 2500))
                    stage = '主力售卖'; age = round(random.uniform(1.5, 4.0), 1)
                if (region, az, rtype) in oversell_pools:
                    server_count = max(server_count, random.randint(1000, 2000))
                    stage = '主力售卖'; age = round(random.uniform(1.0, 3.5), 1)

                # ── 成本计算 ──
                # 算力 = 服务器折旧
                # 4年（48个月）折旧期满后折旧费基本归零，有极小残值；未满4年有±8%波动
                if age >= 4.0:
                    dep_unit = spec['dep'] * random.uniform(0.0, 0.04)  # 残值极小
                else:
                    dep_unit = spec['dep'] * random.uniform(0.92, 1.08)
                server_depreciation_cost = round(dep_unit * server_count, 2)

                # 非算力：DC随AZ波动大，DCN/网络波动小
                dc_cf  = az_dc_factor[region][az]
                dcn_cf = az_dcn_factor[region][az]
                net_cf = az_net_factor[region][az]

                # 特定资源类型的DC额外溢价（个别资源池问题）
                if (region, az, rtype) in rtype_dc_override:
                    dc_cf *= rtype_dc_override[(region, az, rtype)]

                # high_cost_pools：叠加DC额外溢价
                if (region, az, rtype) in high_cost_pools:
                    dc_cf *= 1.35

                dc_cost      = round(spec['dc']  * dc_cf  * random.uniform(0.97, 1.03) * server_count, 2)
                dcn_cost     = round(spec['dcn'] * dcn_cf * random.uniform(0.97, 1.03) * server_count, 2)
                network_cost = round(spec['net'] * net_cf * random.uniform(0.97, 1.03) * server_count, 2)

                compute_cost     = server_depreciation_cost
                non_compute_cost = round(dc_cost + dcn_cost + network_cost, 2)
                cost             = round(compute_cost + non_compute_cost, 2)

                # 分配率 & CPU
                if (region, az, rtype) in oversell_pools:
                    alloc = round(random.uniform(0.83, 0.95), 4)
                    cpu   = round(random.uniform(0.17, 0.28), 4)
                elif (region, az, rtype) in low_alloc_pools:
                    alloc = round(random.uniform(0.28, 0.44), 4)
                    cpu   = round(random.uniform(0.20, 0.36), 4)
                else:
                    alloc = round(random.uniform(0.58, 0.80), 4)
                    cpu   = round(alloc * random.uniform(0.68, 0.90), 4)

                # 流水
                unit_price = spec['price'] * random.uniform(0.97, 1.03)
                revenue    = round(unit_price * server_count * alloc, 2)
                gross_margin = round((revenue - cost * 1.18) / revenue, 4) if revenue > 0 else 0

                rows.append((
                    month, region, az, rtype,
                    cost, compute_cost, non_compute_cost,
                    revenue, gross_margin,
                    cpu, alloc, server_count,
                    age, stage,
                    server_depreciation_cost, dc_cost, dcn_cost, network_cost
                ))

sql = """INSERT INTO cloud_cost
(month, region, az, resource_type,
 cost, compute_cost, non_compute_cost,
 revenue, gross_margin, cpu_usage, allocation_rate, server_count, server_avg_age, stage,
 server_depreciation_cost, dc_cost, dcn_cost, network_cost)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

# ── 额外补充：小台数主力售卖池（1-100台，让气泡大小差异明显）──────────────
small_count_pools = [
    ('华北-北京一', 'az2', 'c7-2',  15),
    ('华北-北京一', 'az3', 'm7-2',  28),
    ('华东-上海一', 'az1', 'c7-4',  45),
    ('华东-上海一', 'az2', 's7-2',   8),
    ('华东-上海二', 'az3', 'c7-2',  62),
    ('华南-广州一', 'az2', 'm7-2',  33),
    ('华南-深圳一', 'az1', 'c7-4',  77),
    ('西南-成都一', 'az3', 's7-2',  19),
    ('西北-西安一', 'az1', 'c7-2',  51),
    ('华中-武汉一', 'az2', 'm7-2',  41),
    ('东北-沈阳一', 'az3', 'c7-4',  88),
    ('华北-北京二', 'az1', 's7-2',   5),
    ('华东-上海一', 'az3', 'c7-2',  72),
    ('华南-广州一', 'az3', 's7-2',  96),
    ('西南-成都一', 'az1', 'm7-2',  24),
]
for month in months:
    for region, az, rtype, srv in small_count_pools:
        spec  = resource_specs[rtype]
        stage = '主力售卖'
        age   = round(random.uniform(1.0, 4.0), 1)
        dep_unit = spec['dep'] * random.uniform(0.92, 1.08)
        dc_cf  = az_dc_factor[region][az]
        dcn_cf = az_dcn_factor[region][az]
        net_cf = az_net_factor[region][az]
        if (region, az, rtype) in rtype_dc_override:
            dc_cf *= rtype_dc_override[(region, az, rtype)]
        server_count = srv + random.randint(-3, 3)
        server_count = max(1, server_count)
        server_depreciation_cost = round(dep_unit * server_count, 2)
        dc_cost      = round(spec['dc']  * dc_cf  * random.uniform(0.97, 1.03) * server_count, 2)
        dcn_cost     = round(spec['dcn'] * dcn_cf * random.uniform(0.97, 1.03) * server_count, 2)
        network_cost = round(spec['net'] * net_cf * random.uniform(0.97, 1.03) * server_count, 2)
        compute_cost     = server_depreciation_cost
        non_compute_cost = round(dc_cost + dcn_cost + network_cost, 2)
        cost             = round(compute_cost + non_compute_cost, 2)
        alloc = round(random.uniform(0.40, 0.75), 4)
        cpu   = round(alloc * random.uniform(0.65, 0.88), 4)
        unit_price   = spec['price'] * random.uniform(0.97, 1.03)
        revenue      = round(unit_price * server_count * alloc, 2)
        gross_margin = round((revenue - cost * 1.18) / revenue, 4) if revenue > 0 else 0
        rows.append((
            month, region, az, rtype,
            cost, compute_cost, non_compute_cost,
            revenue, gross_margin, cpu, alloc, server_count,
            age, stage,
            server_depreciation_cost, dc_cost, dcn_cost, network_cost
        ))

cur.executemany(sql, rows)
conn.commit()
print(f"插入 {len(rows)} 条")

# 验证成本构成
cur.execute("""
SELECT stage,
  ROUND(AVG(compute_cost/cost*100),1) as compute_pct,
  ROUND(AVG(dc_cost/cost*100),1) as dc_pct,
  ROUND(AVG(dcn_cost/cost*100),1) as dcn_pct,
  ROUND(AVG(network_cost/cost*100),1) as net_pct,
  ROUND(AVG(gross_margin*100),1) as avg_gm
FROM cloud_cost WHERE month=202601
GROUP BY stage ORDER BY avg_gm DESC
""")
print("\n-- 成本构成验证 --")
print(f"{'阶段':<12} {'算力%':<8} {'DC%':<8} {'DCN%':<8} {'网络%':<8} {'毛利%'}")
for r in cur.fetchall():
    print(f"{r[0]:<12} {r[1]:<8} {r[2]:<8} {r[3]:<8} {r[4]:<8} {r[5]}")

cur.close(); conn.close()
