package io.crosstalk;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;

/**
 * Optional shared-secret gate on the relay endpoints. If env RELAY_TOKEN is set, every
 * request to /mcp and /api must carry "Authorization: Bearer &lt;RELAY_TOKEN&gt;". If it is
 * unset, the relay is open (a warning is printed at startup) - only acceptable on a fully
 * trusted local network. Strongly recommended whenever the relay is reachable beyond
 * localhost (LAN, VPN, or a public tunnel).
 */
@Configuration
public class RelayAuthConfig {

    private static final String TOKEN = System.getenv("RELAY_TOKEN");

    @Bean
    public FilterRegistrationBean<TokenFilter> relayTokenFilter() {
        FilterRegistrationBean<TokenFilter> reg = new FilterRegistrationBean<>(new TokenFilter());
        reg.addUrlPatterns("/mcp", "/mcp/*", "/api/*");
        reg.setOrder(1);
        return reg;
    }

    static class TokenFilter implements Filter {
        @Override
        public void doFilter(ServletRequest req, ServletResponse res, FilterChain chain)
                throws IOException, ServletException {
            if (TOKEN != null && !TOKEN.isEmpty()) {
                String auth = ((HttpServletRequest) req).getHeader("Authorization");
                if (auth == null || !auth.equals("Bearer " + TOKEN)) {
                    HttpServletResponse http = (HttpServletResponse) res;
                    http.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                    http.setContentType("application/json");
                    http.getWriter().write("{\"error\":\"unauthorized\"}");
                    return;
                }
            }
            chain.doFilter(req, res);
        }
    }
}
