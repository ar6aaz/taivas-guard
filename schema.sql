-- Normalised store for IFIC analysis (DuckDB).
-- One shared core for space and terrestrial notices; domain-specific detail
-- hangs off the core. Units are in column names. Every row keeps its source.

------------------------------------------------------------------ provenance
CREATE TABLE source_file (
    source_id      INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL,     -- 'space_ific' | 'terr_ific' | 'm4' | 'special_xls' | 'rr' | 'ecc'
    issue_no       INTEGER,           -- IFIC number, e.g. 3074 / 3076
    issue_date     DATE,
    file_name      TEXT NOT NULL,
    sha256         TEXT NOT NULL,     -- proves which file a finding came from
    loaded_at      TIMESTAMP DEFAULT current_timestamp
);

------------------------------------------------------------------ core notices
CREATE TABLE notice (
    notice_key     TEXT PRIMARY KEY,  -- e.g. 'S:3074:123456' or 'T:3076:987654'
    domain         TEXT NOT NULL,     -- 'space' | 'terrestrial'
    source_id      INTEGER NOT NULL REFERENCES source_file,
    src_notice_id  TEXT NOT NULL,     -- ntc_id (space) / terrestrial notice id, verbatim
    adm            TEXT NOT NULL,     -- notifying administration, 3-letter ITU code
    name           TEXT,              -- satellite network / station name
    section        TEXT,              -- 'API/A' | 'CR/C' | 'AP30' | 'AP30A' | 'AP30B' | 'PART_I' ...
    action         TEXT,              -- 'ADD' | 'MOD' | 'SUP' as published
    publication_date DATE,
    comment_deadline DATE,            -- publication + 4 months where it applies
    provisions     TEXT[],            -- e.g. ['9.7','9.14'] (space)
    affected_adm   TEXT[]             -- administrations the BR identified (CR/C)
);

-- One row per frequency assignment / group. This is what the filter works on.
CREATE TABLE assignment (
    assignment_key TEXT PRIMARY KEY,  -- notice_key || ':' || src_group_id
    notice_key     TEXT NOT NULL REFERENCES notice,
    src_group_id   TEXT,              -- grp_id (space) or assignment id (terrestrial)
    f_low_mhz      DOUBLE NOT NULL,
    f_high_mhz     DOUBLE NOT NULL,
    bw_khz         DOUBLE,
    direction      TEXT,              -- 'space-to-Earth' | 'Earth-to-space' | 'terrestrial'
    service        TEXT,              -- normalised: 'FSS','BSS','MSS','EESS','LAND_MOBILE',...
    station_class  TEXT,              -- ITU class code, e.g. 'EC','FB','ML'
    emission       TEXT,              -- designation of emission, e.g. '36M0G7W'
    p_dens_max_dbw_hz DOUBLE,         -- space: max power density
    power_dbw      DOUBLE,            -- terrestrial: notified power
    power_type     TEXT,              -- 'ERP' | 'EIRP' | 'P' (as notified)
    gain_max_dbi   DOUBLE,
    raw            JSON               -- untouched source columns for audit
);

------------------------------------------------------------------ geometry
CREATE TABLE space_orbit (
    notice_key     TEXT PRIMARY KEY REFERENCES notice,
    orbit_type     TEXT NOT NULL,     -- 'GSO' | 'NGSO'
    gso_lon_deg    DOUBLE,            -- east positive
    inclination_deg DOUBLE,
    apogee_km      DOUBLE,
    perigee_km     DOUBLE,
    n_planes       INTEGER,
    sats_per_plane INTEGER
);

CREATE TABLE beam (
    beam_key       TEXT PRIMARY KEY,
    notice_key     TEXT NOT NULL REFERENCES notice,
    beam_name      TEXT,
    gain_max_dbi   DOUBLE,
    pointing_lat   DOUBLE,
    pointing_lon   DOUBLE,
    gims_ref       TEXT               -- pointer to contour data if used later
);

CREATE TABLE terr_station (
    assignment_key TEXT PRIMARY KEY REFERENCES assignment,
    lat            DOUBLE NOT NULL,
    lon            DOUBLE NOT NULL,
    site_alt_m     DOUBLE,
    ant_height_m   DOUBLE,
    azimuth_deg    DOUBLE,            -- NULL = omnidirectional
    heff_max_m     DOUBLE,
    polarization   TEXT,
    dist_to_fin_border_km DOUBLE      -- computed once at load
);

------------------------------------------------------------------ change tracking
CREATE TABLE notice_change (
    notice_key     TEXT NOT NULL REFERENCES notice,
    prev_notice_key TEXT,             -- same object in the previous issue, if any
    change_type    TEXT NOT NULL,     -- 'new' | 'modified' | 'withdrawn'
    changed_fields TEXT[],
    PRIMARY KEY (notice_key)
);

------------------------------------------------------------------ national reference data
CREATE TABLE m4_allocation (           -- from m4_allocations.csv
    m4_id          INTEGER PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES source_file,
    f_low_mhz      DOUBLE NOT NULL,
    f_high_mhz     DOUBLE NOT NULL,
    service        TEXT NOT NULL,
    status         TEXT NOT NULL,     -- 'primary' | 'secondary'
    direction      TEXT,
    usage          TEXT,              -- Finnish national use
    notes          TEXT,
    table_page     INTEGER            -- for citations: "M4 table p. 173"
);

CREATE TABLE protected_station (       -- special-interest XLS, plus FIN earth stations
    station_id     TEXT PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES source_file,
    kind           TEXT,              -- 'special_interest' | 'fin_earth_station'
    f_center_mhz   DOUBLE,
    bw_khz         DOUBLE,
    lat            DOUBLE,
    lon            DOUBLE,
    ant_height_m   DOUBLE,
    azimuth_deg    DOUBLE,
    raw            JSON
);

CREATE TABLE fin_satellite_band (      -- bands used by adm='FIN' networks in the SRS
    notice_key     TEXT,
    f_low_mhz      DOUBLE,
    f_high_mhz     DOUBLE,
    direction      TEXT
);

CREATE TABLE limit_mask (              -- Art. 21 / App. 5 / T/R 25-08 as data
    mask_id        TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,     -- 'pfd_art21' | 'pfd_app5' | 'field_strength'
    f_low_mhz      DOUBLE NOT NULL,
    f_high_mhz     DOUBLE NOT NULL,
    service        TEXT,
    orbit_type     TEXT,              -- 'GSO' | 'NGSO' | NULL
    ref_bw_hz      DOUBLE,
    breakpoints    JSON NOT NULL,     -- [[angle_deg, limit_db], ...] or field-strength table
    unit           TEXT NOT NULL,     -- 'dB(W/m2)' | 'dB(uV/m)'
    citation       TEXT NOT NULL      -- e.g. 'RR Art. 21 Table 21-4 (2024 ed.)'
);

------------------------------------------------------------------ outputs
CREATE TABLE finding (
    finding_id     TEXT PRIMARY KEY,
    assignment_key TEXT NOT NULL REFERENCES assignment,
    run_id         TEXT NOT NULL,     -- code version + config hash
    trigger_rules  TEXT[],            -- which filter rules fired
    m4_ids         INTEGER[],         -- matching national allocations
    station_ids    TEXT[],            -- matching protected stations
    method         TEXT,              -- 'pfd_gso' | 'pfd_ngso' | 'p1546'
    calc_inputs    JSON,
    assumptions    TEXT[],
    worst_point    JSON,              -- {lat, lon, angle_deg|distance_km}
    value_db       DOUBLE,
    limit_db       DOUBLE,
    margin_db      DOUBLE,            -- positive = exceeds
    mask_id        TEXT REFERENCES limit_mask,
    severity_score DOUBLE,
    severity_reasons TEXT[],
    citations      JSON,              -- [{doc, clause, page/url}]
    llm_summary    TEXT,
    draft_letter   TEXT,
    review_status  TEXT DEFAULT 'pending'   -- 'pending' | 'accepted' | 'rejected'
);
