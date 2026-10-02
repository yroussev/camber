# Synthetic engine probes

Each probe is a one-day steady frame (fan on).

## fc13_sat_1p5_over_sp_full_cooling (FC13)

full cooling at minimum OA, SAT 1.5 F above its setpoint: inside the G36 SAT tolerance (2 F), outside open-fdd's default (1.15 F)

| engine [profile] | observed | expected |
|---|---|---|
| camber g36_afdd [camber_defaults] | not_fired | not_fired |
| open-fdd pandas [openfdd_defaults] | fired | fired |
| open-fdd pandas [g36] | not_fired | not_fired |
| open-fdd sql [openfdd_defaults] | fired | fired |
| open-fdd sql [g36] | fired | fired |

## fc13_sat_3_over_sp_half_cooling (FC13)

cooling valve at 50 % (not full), minimum OA, SAT 3 F above its setpoint: G36 FC13 is a full-cooling test

| engine [profile] | observed | expected |
|---|---|---|
| camber g36_afdd [camber_defaults] | not_fired | not_fired |
| open-fdd pandas [openfdd_defaults] | fired | fired |
| open-fdd pandas [g36] | fired | fired |
| open-fdd sql [openfdd_defaults] | not_fired | not_fired |
| open-fdd sql [g36] | not_fired | not_fired |

## fc9_oat_4_over_sp_free_cooling (FC9)

free cooling (damper 50 %, valve shut), OAT 4 F above the SAT setpoint: G36 fires above SATSP + 5 F (eps OAT 5 + eps SAT 2 - fan heat 2); open-fdd's default above SATSP + 1.75 F

| engine [profile] | observed | expected |
|---|---|---|
| camber g36_afdd [camber_defaults] | not_fired | not_fired |
| open-fdd pandas [openfdd_defaults] | fired | fired |
| open-fdd pandas [g36] | not_fired | not_fired |
| open-fdd sql [openfdd_defaults] | fired | fired |
| open-fdd sql [g36] | fired | fired |

## fc8_sat_3p5_over_mat_free_cooling (FC8)

free cooling (damper 50 %, valve shut), SAT 3.5 F above MAT: outside open-fdd's default band (|SAT - 0.55 - MAT| > 1.63 F), inside the G36 one (|SAT - 2 - MAT| > 5.39 F). A positive control: FC8's tolerances are reachable in both open-fdd engines, so the G36 profile must silence it, showing the override mechanism is applied

| engine [profile] | observed | expected |
|---|---|---|
| camber g36_afdd [camber_defaults] | not_fired | not_fired |
| open-fdd pandas [openfdd_defaults] | fired | fired |
| open-fdd pandas [g36] | not_fired | not_fired |
| open-fdd sql [openfdd_defaults] | fired | fired |
| open-fdd sql [g36] | not_fired | not_fired |

