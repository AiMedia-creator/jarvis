package de.jarvis.backend.audio;

import java.util.Arrays;
import java.util.UUID;

public record AudioFrame(UUID streamId,long epoch,long sequence,String mime,byte[] payload) {
  public AudioFrame { payload=Arrays.copyOf(payload,payload.length); }
  public byte[] payloadCopy(){return Arrays.copyOf(payload,payload.length);}
}
