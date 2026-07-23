package com.workflow.uniapp.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class StyleSelection {
    @NotBlank
    private java.util.List<String> styleIds;
    private String colors;
}
