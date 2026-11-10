package de.jarvis.backend.security;

import de.jarvis.backend.config.JarvisProperties;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SessionServiceTest {
    @Test void tokenAndSecureCookieAreEnforced() {
        JarvisProperties props = new JarvisProperties();
        props.setSessionToken("unit-test-session-token");
        props.setSecureCookie(true);
        props.validate();
        SessionService sessions = new SessionService(props);
        assertFalse(sessions.originAllowed(null));
        assertFalse(sessions.originAllowed("https://evil.example"));
        assertTrue(sessions.originAllowed("http://localhost:4173"));
        assertNull(sessions.create("wrong").block());
        var cookie = sessions.create("unit-test-session-token").block();
        assertNotNull(cookie);
        assertTrue(cookie.isSecure());
        assertTrue(cookie.isHttpOnly());
        assertTrue(sessions.authorized("JARVIS_SESSION=" + cookie.getValue()));
        assertFalse(sessions.authorized("JARVIS_SESSION=invalid"));
    }
    @Test void nonlocalBindNeedsTokenAndWildcardOriginIsRejected() {
        JarvisProperties props = new JarvisProperties();
        props.setBind("0.0.0.0");
        assertThrows(IllegalStateException.class, props::validate);
        props.setSessionToken("unit-test-session-token");
        props.setAllowedOrigins("*");
        assertThrows(RuntimeException.class, props::validate);
    }
}
