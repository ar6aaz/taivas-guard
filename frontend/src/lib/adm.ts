// ITU administration codes seen in the sample issues. Unknown codes fall back to the code itself.
// The IFIC's own ref_adm table should replace this once the pipeline loads it.
const NAMES: Record<string, string> = {
  ARS: 'Saudi Arabia', AZE: 'Azerbaijan', B: 'Brazil', BEL: 'Belgium', BLR: 'Belarus', BUL: 'Bulgaria', CAN: 'Canada', CHN: 'China', D: 'Germany', E: 'Spain', EST: 'Estonia', F: 'France', FIN: 'Finland', G: 'United Kingdom', GRC: 'Greece', HOL: 'Netherlands', I: 'Italy', IND: 'India', INS: 'Indonesia', ISR: 'Israel', J: 'Japan', KAZ: 'Kazakhstan', KOR: 'Republic of Korea', LTU: 'Lithuania', LUX: 'Luxembourg', LVA: 'Latvia', MNG: 'Mongolia', NOR: 'Norway', PAK: 'Pakistan', PHL: 'Philippines', POL: 'Poland', POR: 'Portugal', QAT: 'Qatar', RUS: 'Russian Federation', S: 'Sweden', TUR: 'Türkiye', UKR: 'Ukraine', USA: 'United States', UZB: 'Uzbekistan', VTN: 'Viet Nam',
};
export const admName = (code: string) => (NAMES[code] ? `${NAMES[code]} (${code})` : code);
