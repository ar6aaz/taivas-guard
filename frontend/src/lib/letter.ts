import type { Finding } from '../api/types';
import { admName } from './adm';
import { dateOr, refBandwidth, signed } from './format';

/** Fixed template filled only from the finding. No AI is involved yet. */
export function draftLetter(f: Finding): string {
  const notices = [f.sourceId, ...f.relatedSourceIds].filter(Boolean).join(', ');
  const header = `DRAFT FOR EXPERT REVIEW. NOT SENT.

From: Finnish Transport and Communications Agency Traficom
To: Administration of ${admName(f.adm)}
Copy: ITU Radiocommunication Bureau
Re: ${f.title}, BR IFIC ${f.issue}${f.sections.length ? `, ${f.sections.join(', ')}` : ''}, notice ID ${notices}
`;
  const close = `
[Expert: confirm the provision, the Finnish assignments relied on, and the assumptions before sending.]

Yours faithfully,
[Name, title]
Traficom, Spectrum and Space`;

  if (f.calc) {
    const c = f.calc;
    return `${header}
Dear colleagues,

Finland has examined the above filing published in BR IFIC ${f.issue}.

Our screening calculates a power flux-density of ${signed(c.pfd)} dB(W/m²) in ${refBandwidth(c.refBandwidthHz)} at an angle of arrival of ${c.angleDeg}° over Finland, in the band ${c.fMinMhz}–${c.fMaxMhz} MHz (beam ${c.beam}, emission ${c.emission}). This is ${c.marginDb.toFixed(1)} dB above the value of ${signed(c.limit)} dB(W/m²) in ${f.citations[0]}, under free-space assumptions and using the maximum notified antenna gain.

This indicates potential unacceptable interference to Finnish assignments in this band [Expert: list the assignments relied on]. We would welcome confirmation of the antenna gain towards Finland and the operational orbit, and ask that these assignments are operated within the applicable limits over Finnish territory.
${close}`;
  }
  if (f.result && f.result.marginDb !== null) {
    const r = f.result;
    return `${header}
Dear colleagues,

Finland has examined the above filing published in BR IFIC ${f.issue}.

Our calculation (${r.method}) gives ${signed(r.valueDb ?? 0)} dB against a value of ${signed(r.limitDb ?? 0)} dB${f.citations[0] ? ` in ${f.citations[0]}` : ''}, a margin of ${signed(r.marginDb ?? 0)} dB, under the stated assumptions.

[Expert: state the concern, the Finnish assignments relied on, and any suggestions for resolving the matter.]
${close}`;
  }
  if (f.coordProvisions.length) {
    return `${header}
Dear colleagues,

Finland has been identified under ${f.coordProvisions.join(', ')} in relation to the above filing${f.band ? `, in the band ${f.band}` : ''}.

[Expert: state agreement or disagreement under the applicable provision, with information on the Finnish assignments concerned and any suggestions for resolving the matter.]
${close}`;
  }
  if (f.kind === 'station-rx-overlap') {
    return `${header}
Dear colleagues,

Finland has examined the above filing. It includes space-to-Earth transmissions in the following band(s), which overlap frequencies used by Finnish stations:

${f.bands.map((b) => `- ${b.lowMhz}–${b.highMhz} MHz`).join('\n')}

This is a possible concern only. The comparison is by frequency: no power flux-density has been calculated, and neither beam coverage nor station location has been checked.

[Expert: decide whether this needs raising, and state the Finnish assignments relied on.]
${close}`;
  }
  if (f.bands.length) {
    return `${header}
Dear colleagues,

Finland has examined the above filing. It includes space-to-Earth transmissions in the following band(s), where Finland's frequency table has no space-to-Earth allocation:

${f.bands.map((b) => `- ${b.lowMhz}–${b.highMhz} MHz (Finnish use: ${b.finnishUse || 'see M4'})`).join('\n')}

This is a possible concern only. No power flux-density has been calculated, and beam coverage over Finland hasn't been checked.

[Expert: decide whether this needs raising, and state the Finnish assignments relied on.]
${close}`;
  }
  const t = f.terrestrial;
  if (t) {
    return `${header}
Dear colleagues,

Finland has examined the notice for ${t.site} (${t.fMhz} MHz), about ${t.borderKm} km from the Finnish border according to our approximate border model.

[Expert: this draft can't be completed until the P.1546 field strength has been calculated and compared with the applicable coordination criterion.]
${close}`;
  }
  return `${header}
Dear colleagues,

[Expert: describe the concern.]
${close}`;
}

export function windowText(deadline: string | null): string {
  return dateOr(deadline, 'No window');
}
