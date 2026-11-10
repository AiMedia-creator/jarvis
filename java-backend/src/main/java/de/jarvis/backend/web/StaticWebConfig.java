package de.jarvis.backend.web;
import de.jarvis.backend.config.JarvisProperties;
import java.nio.file.Path;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.config.ResourceHandlerRegistry;
import org.springframework.web.reactive.config.WebFluxConfigurer;
@Configuration
public class StaticWebConfig implements WebFluxConfigurer {
  private final JarvisProperties props; public StaticWebConfig(JarvisProperties p){props=p;}
  public void addResourceHandlers(ResourceHandlerRegistry r){String location=Path.of(props.getStaticDir()).toAbsolutePath().normalize().toUri().toString();r.addResourceHandler("/**").addResourceLocations(location);}
}
