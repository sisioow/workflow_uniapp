package com.workflow.uniapp.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "workflow.paths")
public class PathsConfig {
    private String uniappTemplateDir = "";
    private String uniappOutputDir = "";
}
