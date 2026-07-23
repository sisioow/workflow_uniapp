package com.workflow.uniapp.integration.stitch;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "workflow.stitch")
public class StitchConfig {
    private String mcpUrl = "https://stitch.googleapis.com/mcp";
    private String apiKey = "";
    private String oauthToken = "";
    private String oauthClientId = "";
    private String oauthClientSecret = "";
}
