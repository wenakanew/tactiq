# Third-party rights and notices

[Documentation](../README.md) · [Original-source MIT license](../../LICENSE)

The root MIT license covers original Tactiq source. It does not relicense dependencies, model weights, datasets, uploaded footage, trademarks, or cloud services.

| Material | Rights boundary |
| --- | --- |
| Python and JavaScript dependencies | Preserve upstream copyright/license notices when redistributing. Exact resolved versions are in [requirements.lock](../../requirements.lock) and [web lockfile](../../apps/web/package-lock.json). |
| FFmpeg/FFprobe and container packages | Licensing depends on the exact binary/build and enabled codecs. Review the distributed image and its notices, not just application manifests. |
| ONNX detector artifacts | Obtain separately, verify SHA-256, and accept the artifact's own terms. [YOLOX upstream](https://github.com/Megvii-BaseDetection/YOLOX) publishes an Apache-2.0 project license; this is not a blanket grant over all weights/training data. |
| Uploaded/downloaded football video | Operators must hold analysis, playback, and publication rights. An accessible URL or rights-confirmation checkbox does not establish a legal license. |
| Microsoft Foundry, Azure Speech, provider resolvers | Service terms, usage limits, and applicable provider rules remain separate from MIT. No access-restriction bypass is authorized. |
| League, club, and platform names | Descriptive references do not imply affiliation, endorsement, or licensed branding/data access. |

Generated media and prior tracked football footage were removed from the release tree after exact-file preservation. Copies may remain in Git history; removal is not proof that earlier publication was licensed or erased.

## Before distributing a release

Review the actual dependency/image inventory and collect all required upstream license texts/notices for the artifacts being shipped. Record model and dataset provenance separately. This page is a rights boundary and checklist, not a completed SBOM, exhaustive license audit, or claim that third-party obligations have been discharged.
