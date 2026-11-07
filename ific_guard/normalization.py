"""Materialize a small, typed screening table once per imported IFIC."""


def number(alias, field):
    return f"TRY_CAST(replace({alias}.raw->>'{field}', ',', '.') AS DOUBLE)"


def normalize(connection, source_key, domain):
    if domain == "space":
        _space(connection, source_key)
    else:
        _terrestrial(connection, source_key)


def _space(connection, source_key):
    low, high, width = (number("g", name) for name in ("freq_min", "freq_max", "bdwdth"))
    connection.execute(f"""
        INSERT INTO ific_assignment
        WITH r AS (SELECT * FROM ific_raw WHERE source_key = ?),
        groups AS (SELECT *, raw->>'grp_id' AS gid, raw->>'ntc_id' AS nid FROM r WHERE table_name='grp'),
        notices AS (SELECT raw, raw->>'ntc_id' AS nid FROM r WHERE table_name='notice'),
        summaries AS (SELECT raw, raw->>'ntc_id' AS nid FROM r WHERE table_name='com_el'),
        geo AS (SELECT raw, raw->>'ntc_id' AS nid FROM r WHERE table_name='geo'),
        fin AS (SELECT DISTINCT raw->>'grp_id' AS gid FROM r
                WHERE table_name='provn' AND upper(trim(raw->>'adm'))='FIN'),
        notice_rows AS (
            SELECT r.table_name, r.raw, coalesce(r.raw->>'ntc_id', g.nid) AS nid
            FROM r LEFT JOIN groups g ON r.raw->>'grp_id'=g.gid
        ),
        fingerprints AS (
            SELECT nid, sha256(string_agg(table_name || ':' || sha256(raw::VARCHAR), '|'
                       ORDER BY table_name, sha256(raw::VARCHAR))) AS fingerprint
            FROM notice_rows WHERE nid IS NOT NULL GROUP BY nid
        ),
        prepared AS (
            SELECT g.source_key, g.nid || ':' || g.gid AS assignment_id, g.nid AS notice_id,
                   'space' AS domain,
                   CASE WHEN {low}={high} AND {width}>0 THEN {low}-{width}/2000 ELSE {low} END AS f_low_mhz,
                   CASE WHEN {low}={high} AND {width}>0 THEN {high}+{width}/2000 ELSE {high} END AS f_high_mhz,
                   coalesce(nullif(upper(trim(n.raw->>'adm')),''), nullif(upper(trim(c.raw->>'adm')),'')) AS adm,
                   CASE WHEN upper(trim(coalesce(n.raw->>'ntc_type', c.raw->>'ntc_type'))) IN ('G','N','S','T','R')
                        THEN upper(trim(coalesce(n.raw->>'ntc_type', c.raw->>'ntc_type'))) ELSE NULL END AS ntc_type,
                   CASE WHEN upper(trim(g.raw->>'emi_rcp')) IN ('E','R')
                        THEN upper(trim(g.raw->>'emi_rcp')) ELSE NULL END AS direction,
                   NULL::VARCHAR AS station_class,
                   fin.gid IS NOT NULL AS fin_listed,
                   coalesce({number('geo', 'long_nom')}, {number('c', 'long_nom')}) AS gso_lon_deg,
                   NULL::DOUBLE AS latitude_deg, NULL::DOUBLE AS longitude_deg,
                   fp.fingerprint AS raw_hash,
                   CASE WHEN n.nid IS NULL THEN 'missing_notice' ELSE '' END AS review_reason,
                   coalesce(nullif(upper(trim(n.raw->>'act_code')),''), nullif(upper(trim(c.raw->>'act_code')),'')) AS publication_action
            FROM groups g LEFT JOIN notices n ON g.nid=n.nid
            LEFT JOIN summaries c ON g.nid=c.nid LEFT JOIN geo ON g.nid=geo.nid
            LEFT JOIN fin ON g.gid=fin.gid LEFT JOIN fingerprints fp ON g.nid=fp.nid
        )
        SELECT * FROM prepared
    """, [source_key])


def _terrestrial(connection, source_key):
    # freq_min/max are MHz in the supported BRIFIC extract. Do not guess
    # bandwidth units when a terrestrial range is missing or zero-width.
    connection.execute(f"""
        INSERT INTO ific_assignment
        WITH r AS (SELECT * FROM ific_raw WHERE source_key=?),
        t AS (SELECT *, raw->>'terrakey' AS tid FROM r WHERE table_name='fxm_terra'),
        geo AS (SELECT raw, raw->>'geo_key' AS gid FROM r WHERE table_name='fxm_geo'),
        ants AS (
            SELECT raw->>'terrakey' AS tid,
                   string_agg(sha256(raw::VARCHAR), '|' ORDER BY sha256(raw::VARCHAR)) AS fingerprint
            FROM r WHERE table_name='fxm_ant' GROUP BY tid
        )
        SELECT t.source_key, t.tid, t.tid, 'terrestrial',
               {number('t', 'freq_min')}, {number('t', 'freq_max')},
               nullif(upper(trim(t.raw->>'adm')),''), NULL, 'E', nullif(upper(trim(t.raw->>'stn_cls')),''), false,
               NULL, {number('geo', 'lat_dec')}, {number('geo', 'long_dec')},
               sha256(t.raw::VARCHAR || coalesce(geo.raw::VARCHAR,'') || coalesce(ants.fingerprint,'')),
               CASE WHEN geo.gid IS NULL THEN 'missing_location' ELSE '' END,
               upper(trim(t.raw->>'intent'))
        FROM t LEFT JOIN geo ON t.raw->>'geo_key'=geo.gid LEFT JOIN ants ON t.tid=ants.tid
    """, [source_key])
