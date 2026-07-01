// Init script used INSIDE the air-gapped network to resolve every dependency and plugin from
// Artifactory instead of the public internet.
//
// Run via:
//   ARTIFACTORY_MAVEN_URL="https://artifactory.internal/artifactory/monk-offline-maven" \
//     ./gradlew -I offline.init.gradle.kts build
//
// Do NOT pass --offline: the internal network (and therefore Artifactory) IS reachable;
// --offline would block Artifactory too.

val artifactoryMavenUrl: String =
    System.getenv("ARTIFACTORY_MAVEN_URL")
        ?: System.getProperty("artifactoryMavenUrl")
        ?: throw GradleException(
            "Set ARTIFACTORY_MAVEN_URL (or -DartifactoryMavenUrl=...) to the Artifactory Maven repo URL"
        )

settingsEvaluated {
    pluginManagement.repositories.apply {
        clear()
        maven { setUrl(artifactoryMavenUrl) }
    }
    dependencyResolutionManagement.repositories.apply {
        clear()
        maven { setUrl(artifactoryMavenUrl) }
    }
}
