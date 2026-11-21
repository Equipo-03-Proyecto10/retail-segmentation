# F27-03 (#857): the permitted commercial profile at 375 dp

Rendered on the JVM by `ScreenshotsTest` (Robolectric native graphics, `w375dp-h800dp-xxhdpi`,
1125 x 2400 px), light and dark, from `clients/android`:

    SCREENSHOTS=1 ./gradlew testDebugUnitTest --tests '*ScreenshotsTest.commercial*'

| State | Light | Dark |
|---|---|---|
| Assigned, personalization allowed | [light](857-android-commercial-profile-assigned-light.png) | [dark](857-android-commercial-profile-assigned-dark.png) |
| Unassigned result, objected to personalization | [light](857-android-commercial-profile-objected-unassigned-light.png) | [dark](857-android-commercial-profile-objected-unassigned-dark.png) |
| No open assignment | [light](857-android-commercial-profile-no-open-assignment-light.png) | [dark](857-android-commercial-profile-no-open-assignment-dark.png) |

What each shows against the acceptance criteria:

- No R, F or M score, no `run_id`, and for a seller no email or phone: the models the app parses
  into declare none of them (`IdentifyViewModelTest.anExcludedElementIsNeitherKeptNorShown`).
- An objection to personalization is a warning badge with "Nothing is offered to this customer as
  personalized", and inferred interests are not shown.
- An unassigned result and a customer with no open assignment are stated as segments-service
  states them, with no label filled in.
