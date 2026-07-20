CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY,        -- seen.json 키와 동일 형식: "APT:2026000316" / "LH:2015122300020399"
  source TEXT NOT NULL,       -- APT | REMND | OFCTL | LH (id 접두사와 동일, no_remnd/category 필터용)
  type TEXT,                  -- seen.json meta의 "type"과 동일 (예: "APT 일반분양", "LH 임대")
  name TEXT,
  region TEXT,
  rcept_bgnde TEXT,
  rcept_endde TEXT,           -- 만료 판정·정렬 기준 (LH는 마감일이 이 컬럼에 옴)
  spsply_bgnde TEXT,
  spsply_endde TEXT,
  przwner_de TEXT,
  url TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_endde ON listings(rcept_endde);
CREATE INDEX IF NOT EXISTS idx_listings_region ON listings(region);
