package de.jarvis.backend.web;

import static org.springframework.test.web.reactive.server.WebTestClient.*;
import static org.junit.jupiter.api.Assertions.*;
import de.jarvis.backend.JarvisApplication;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.reactive.AutoConfigureWebTestClient;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.reactive.server.WebTestClient;

@SpringBootTest(classes=JarvisApplication.class,webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={"jarvis.demo=true","jarvis.auth-required=false"})
@AutoConfigureWebTestClient
class ApiControllerTest {
  @Autowired WebTestClient web;
  @Test void configContainsDemoCitiesAndCameras(){web.get().uri("/api/config").exchange().expectStatus().isOk().expectBody().jsonPath("$.demo").isEqualTo(true).jsonPath("$.auth_required").isEqualTo(false).jsonPath("$.cities").isArray().jsonPath("$.cameras").isArray();}
}
