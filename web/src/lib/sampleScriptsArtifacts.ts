/** Detect robots.txt / llms.txt / json-ld mentions that map to Workshop samples. */
export const SAMPLE_SCRIPT_ARTIFACT_RE =
  /robots\.txt|llms\.txt|json-?ld(?:\.txt)?/i;

export function mentionsSampleScriptArtifact(...texts: string[]): boolean {
  return texts.some((text) => SAMPLE_SCRIPT_ARTIFACT_RE.test(text));
}
