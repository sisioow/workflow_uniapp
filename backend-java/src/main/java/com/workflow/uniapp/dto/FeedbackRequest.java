package com.workflow.uniapp.dto;

import lombok.Data;
import lombok.NoArgsConstructor;
import lombok.AllArgsConstructor;

/** 用户反馈请求 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class FeedbackRequest {
    private String rating;    // good | ok | bad
    private int score;        // 1-5
    private String comment;
}
