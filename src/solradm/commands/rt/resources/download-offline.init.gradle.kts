// Init script used on the INTERNET-CONNECTED machine to force a complete dependency download.
//
// Run via:
//   ./gradlew -g <isolated-gradle-home> -I scripts/download-offline.init.gradle.kts \
//             --refresh-dependencies build resolveOfflineDependencies
//
// It does two things:
//   1. Pins resolution to canonical public remotes (drops mavenLocal so the produced bundle
//      is reproducible and independent of the developer's ~/.m2).
//   2. Adds a `resolveOfflineDependencies` task to every project that additionally pulls the
//      POMs, -sources and -javadoc artifacts for every resolved component into the cache, so
//      the cache harvest captures them.

import org.gradle.api.artifacts.ConfigurationContainer
import org.gradle.api.artifacts.component.ComponentIdentifier
import org.gradle.jvm.JvmLibrary
import org.gradle.language.base.artifact.SourcesArtifact
import org.gradle.language.java.artifact.JavadocArtifact
import org.gradle.maven.MavenModule
import org.gradle.maven.MavenPomArtifact

settingsEvaluated {
    pluginManagement.repositories.apply {
        clear()
        gradlePluginPortal()
        mavenCentral()
    }
    dependencyResolutionManagement.repositories.apply {
        clear()
        mavenCentral()
        gradlePluginPortal()
    }
}

allprojects {
    tasks.register("resolveOfflineDependencies") {
        // Resolution must happen at execution time, not configuration time.
        notCompatibleWithConfigurationCache("Resolves all configurations eagerly at execution time")
        doLast {
            val ids = LinkedHashSet<ComponentIdentifier>()

            fun collect(container: ConfigurationContainer) {
                container.filter { it.isCanBeResolved }.forEach { cfg ->
                    try {
                        cfg.incoming
                            .artifactView { isLenient = true }
                            .artifacts.artifacts
                            .forEach { ids.add(it.id.componentIdentifier) }
                    } catch (e: Exception) {
                        logger.warn("Skipping configuration '${cfg.name}' in $path: ${e.message}")
                    }
                }
            }

            // Project dependencies (runtime, compile, test, annotation processors, ...) and the
            // buildscript classpath (Gradle plugins and their transitive dependencies).
            collect(configurations)
            collect(buildscript.configurations)

            if (ids.isEmpty()) {
                logger.lifecycle("No external components to resolve for $path")
                return@doLast
            }

            // Force POMs into the cache.
            dependencies.createArtifactResolutionQuery()
                .forComponents(ids)
                .withArtifacts(MavenModule::class.java, MavenPomArtifact::class.java)
                .execute()

            // Force -sources and -javadoc jars into the cache (best effort; not every module
            // publishes them).
            dependencies.createArtifactResolutionQuery()
                .forComponents(ids)
                .withArtifacts(JvmLibrary::class.java, SourcesArtifact::class.java, JavadocArtifact::class.java)
                .execute()

            logger.lifecycle("Resolved offline artifacts for $path (${ids.size} components)")
        }
    }
}
