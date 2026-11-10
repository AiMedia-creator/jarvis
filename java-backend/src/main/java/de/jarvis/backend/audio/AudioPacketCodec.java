package de.jarvis.backend.audio;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;

public final class AudioPacketCodec {
  private final ObjectMapper mapper; public AudioPacketCodec(ObjectMapper mapper){this.mapper=mapper;}
  public byte[] encode(AudioFrame frame){try{var h=new LinkedHashMap<String,Object>();h.put("type","AUDIO");h.put("stream_id",frame.streamId().toString());h.put("sequence",frame.sequence());h.put("mime",frame.mime());byte[] json=mapper.writeValueAsBytes(h);return ByteBuffer.allocate(4+json.length+frame.payload().length).putInt(json.length).put(json).put(frame.payload()).array();}catch(Exception e){throw new IllegalStateException("audio encoding failed",e);}}
  public static boolean validPcmPacket(byte[] bytes){return bytes!=null&&bytes.length<=640&&bytes.length%2==0;}
}
