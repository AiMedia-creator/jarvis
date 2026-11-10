package de.jarvis.backend.provider;
import java.util.ArrayList;
import java.util.List;
public final class SentenceSplitter {
  /** Der allererste Abschnitt darf schon an einem Komma enden, damit die Stimme früher startet. */
  static final int FIRST_CHUNK_MIN = 45;
  private final StringBuilder pending=new StringBuilder();
  private boolean firstDone=false;
  public List<String> accept(String chunk){pending.append(chunk);List<String> out=new ArrayList<>();int start=0;for(int i=0;i<pending.length();i++){char c=pending.charAt(i);if((c=='.'||c=='!'||c=='?'||c=='。'||c=='！'||c=='？')&&(i+1==pending.length()||Character.isWhitespace(pending.charAt(i+1)))){String s=pending.substring(start,i+1).trim();if(!s.isEmpty())out.add(s);start=i+1;}}if(start>0){pending.delete(0,start);}if(!out.isEmpty())firstDone=true;
    if(!firstDone&&pending.length()>=FIRST_CHUNK_MIN){int cut=pending.indexOf(", ",30);if(cut!=-1){String s=pending.substring(0,cut+1).trim();if(!s.isEmpty()){out.add(s);firstDone=true;}pending.delete(0,cut+2);}}
    return out;}
  public String flush(){String s=pending.toString().trim();pending.setLength(0);return s;}
}
