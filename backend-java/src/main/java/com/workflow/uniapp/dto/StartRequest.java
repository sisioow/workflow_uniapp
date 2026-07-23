package com.workflow.uniapp.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class StartRequest {
    @NotBlank
    @Size(max = 60)
    private String appName;

    @NotBlank
    @Size(max = 40)
    @Pattern(regexp = "^[a-z0-9_-]+$")
    private String projectDir;

    private String llmModel;
}
