# 音声モデルの第三者表示

## Silero VAD

- source: https://github.com/snakers4/silero-vad
- license: MIT
- model: `silero_vad.onnx`

Silero VAD のモデルと関連コードは Silero Team が MIT License で公開している。

## ERes2NetV2 / 3D-Speaker

- source: https://modelscope.cn/models/iic/speech_eres2netv2_sv_zh-cn_16k-common/summary
- code: https://github.com/modelscope/3D-Speaker
- ONNX conversion: https://github.com/k2-fsa/sherpa-onnx/tree/master/scripts/3dspeaker
- code license: Apache License 2.0
- pretrained model license: Apache License 2.0
- model: `eres2netv2.onnx`

3D-Speakerの実装と、このERes2NetV2配布モデルはApache License 2.0の条件に従う。
ライセンス全文は[LICENSE-ERes2NetV2.md](LICENSE-ERes2NetV2.md)に同梱する。
モデルカードのlicense表示は[公式README](https://modelscope.cn/api/v1/models/iic/speech_eres2netv2_sv_zh-cn_16k-common/repo?Revision=master&FilePath=README.md)を参照する。
ONNX変換はsherpa-onnxが公開したものを同梱している。
