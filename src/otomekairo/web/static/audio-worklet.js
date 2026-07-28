class OtomeKairoPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputSamples = [];
    this.outputSamples = [];
    this.readPosition = 0;
    this.ratio = sampleRate / 16000;
  }

  process(inputs) {
    const channels = inputs[0] || [];
    if (channels.length === 0 || channels[0].length === 0) {
      return true;
    }

    const frameCount = channels[0].length;
    for (let frame = 0; frame < frameCount; frame += 1) {
      let sample = 0;
      for (const channel of channels) {
        sample += channel[frame] || 0;
      }
      this.inputSamples.push(sample / channels.length);
    }

    while (this.readPosition + 1 < this.inputSamples.length) {
      const lowerIndex = Math.floor(this.readPosition);
      const fraction = this.readPosition - lowerIndex;
      const lower = this.inputSamples[lowerIndex];
      const upper = this.inputSamples[lowerIndex + 1];
      this.outputSamples.push(lower + ((upper - lower) * fraction));
      this.readPosition += this.ratio;

      if (this.outputSamples.length === 320) {
        const pcm = new Int16Array(320);
        for (let index = 0; index < pcm.length; index += 1) {
          const value = Math.max(-1, Math.min(1 - (1 / 32768), this.outputSamples[index]));
          pcm[index] = Math.round(value * 32768);
        }
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
        this.outputSamples = [];
      }
    }

    const consumedSamples = Math.floor(this.readPosition);
    if (consumedSamples > 0) {
      this.inputSamples.splice(0, consumedSamples);
      this.readPosition -= consumedSamples;
    }
    return true;
  }
}

registerProcessor("otomekairo-pcm-processor", OtomeKairoPcmProcessor);
