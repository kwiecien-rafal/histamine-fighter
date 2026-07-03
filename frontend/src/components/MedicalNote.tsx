// One-line disclaimer rendered wherever a verdict or meal suggestion could be acted
// on. The full disclaimer lives in the footer; this keeps it in sight at the decision.
export function MedicalNote() {
  return (
    <p className="text-xs text-stone-500">
      Informational only, not medical advice — tolerance varies from person to person;
      when in doubt, ask your clinician.
    </p>
  );
}
