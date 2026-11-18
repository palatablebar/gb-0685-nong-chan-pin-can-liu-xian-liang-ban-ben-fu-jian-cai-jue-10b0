TITLE='农产品残留限量版本与复检裁决'
SHORT='residuelimit'
RULE_VERSION='2026.11.18-v2'
MODULES=[('m1_ingest', '报告采信'), ('m2_mapping', '项目映射'), ('m3_versions', '限量选版'), ('m4_segments', '检出量换算'), ('m5_calculate', '复检裁决'), ('m6_reconcile', '批次判级'), ('m7_archive', '报告归档')]
REQUIRED=['id', 'sample_id', 'batch', 'product', 'analyte', 'method', 'version', 'sampled', 'reported', 'result', 'unit', 'lod']
NUMERIC=['version', 'lod']
DATES=['sampled', 'reported']
TIMES=[]
# 分送检测站。缺失时视为单站来源，保持旧样例与旧结论不变。
STATION_FIELD='station'
DEFAULT_STATION='本检测站'
ARBITER_STATION='仲裁站'
SAMPLES=('dirty.csv','clean.csv','legacy.csv','cross_station.csv')
