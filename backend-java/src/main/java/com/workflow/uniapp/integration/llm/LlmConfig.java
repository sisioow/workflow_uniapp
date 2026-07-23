package com.workflow.uniapp.integration.llm;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "workflow.llm")
public class LlmConfig {
    private String provider = "openai";
    private String baseUrl = "http://192.168.100.231:8080/v1";
    private String apiKey = "";
    private String model = "deepseek/deepseek-v4-flash";
}
