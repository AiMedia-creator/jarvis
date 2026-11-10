class JarvisMicProcessor extends AudioWorkletProcessor {
  constructor(){ super(); this.buf=[]; this.phase=0; }
  process(inputs){ const input=inputs[0]?.[0]; if(!input)return true; for(const sample of input){ this.buf.push(sample); if(this.buf.length>=128){ this.port.postMessage({type:'samples',samples:new Float32Array(this.buf.splice(0,128))}); }} return true; }
}
registerProcessor('jarvis-mic', JarvisMicProcessor);
