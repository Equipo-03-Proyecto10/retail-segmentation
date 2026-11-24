# F29-05 the suites

Started 2026-11-24T03:49:56Z, finished 2026-11-24T03:51:16Z.
2 checks, 2 held, 0 did not.

- **commit:** `fdf0caedb301d5308ea804ff9cb9bb5b43ce73cb`
- **pytest:** `uv run --frozen pytest -q -rs -p no:cacheprovider --junitxml=/tmp/claude-1000/s1-f2905/suites-android/experimentation-junit.xml tests/test_validation_pipeline.py tests/test_validation_statistics.py`
- **gradle:** `./gradlew --max-workers=2 -Dorg.gradle.jvmargs=-Xmx2g -Pkotlin.daemon.jvmargs=-Xmx768m --console=plain testDebugUnitTest --tests *ExplainedRecommendationsTest --tests *PromotionsViewModelTest`

| Criterion | Case | Expected | Actual | Evidence | |
|---|---|---|---|---|---|
| Campaña A/B | experimentation pipeline validation (F25-14, #845): segment and consent changes after assignment move no arm; overlapping experiments contaminate no one; a sale counts once | exit 0, at least one case, no failure, error or skip | exit 0, {'tests': 14, 'failures': 0, 'errors': 0, 'skipped': 0} | [1](experimentation-pytest.log), [2](experimentation-junit.xml) | ✓ |
| Exposición y conversión | Android (#860, #861): one impression per item shown, a tap is a separate click, loading a list records no exposure and one shown offer queues one | exit 0, both test classes ran, no failure, error or skip | exit 0, 2 classes, {'tests': 17, 'failures': 0, 'errors': 0, 'skipped': 0} | [1](android-gradle.log) | ✓ |
