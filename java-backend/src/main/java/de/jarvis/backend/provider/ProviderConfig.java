package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.config.JarvisProperties;
import io.netty.channel.ChannelOption;
import java.time.Duration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

@Configuration
public class ProviderConfig {
    private WebClient.Builder configure(WebClient.Builder builder) {
        return builder.clone().clientConnector(new ReactorClientHttpConnector(HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 5000)
                .responseTimeout(Duration.ofSeconds(30))))
                .codecs(codecs -> codecs.defaultCodecs().maxInMemorySize(2 * 1024 * 1024));
    }

    @Bean
    PythonBridgeProvider pythonBridgeProvider(WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper) {
        return new PythonBridgeProvider(configure(builder), props, mapper);
    }

    @Bean
    @Primary
    GenerationProvider generationProvider(PythonBridgeProvider bridge, WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper) {
        if ("python".equalsIgnoreCase(props.getGenerationBackend())) {
            if (!props.isDemo() && props.getBridgeToken().isBlank())
                throw new IllegalStateException("Python-Bridge benötigt JARVIS_BRIDGE_TOKEN außerhalb des Demo-Modus.");
            return bridge;
        }
        return props.isDemo() ? DemoProviders.generation() : new AnthropicSseProvider(configure(builder), props, mapper);
    }

    @Bean
    TtsProvider ttsProvider(WebClient.Builder builder, JarvisProperties props) {
        if (props.isDemo()) return DemoProviders.tts();
        if ("python".equalsIgnoreCase(props.getGenerationBackend())) {
            return new PythonBridgeTtsProvider(configure(builder), props);
        }
        return new ElevenLabsTtsProvider(configure(builder), props);
    }

    @Bean
    SttProvider sttProvider(WebClient.Builder builder, JarvisProperties props) {
        return new SttProvider(configure(builder), props);
    }
}
