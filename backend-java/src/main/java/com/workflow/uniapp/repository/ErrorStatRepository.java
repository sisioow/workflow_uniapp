package com.workflow.uniapp.repository;

import com.workflow.uniapp.entity.ErrorStat;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Optional;

interface ErrorStatRepository extends JpaRepository<ErrorStat, Long> {
    Optional<ErrorStat> findByErrorKey(String errorKey);

    @Transactional
    default void increment(String errorKey, String sample) {
        findByErrorKey(errorKey).ifPresentOrElse(
            stat -> {
                stat.setCount(stat.getCount() + 1);
                stat.setLastSeen(LocalDateTime.now());
                stat.setSample(sample != null && sample.length() > 500 ? sample.substring(0, 500) : sample);
                save(stat);
            },
            () -> save(ErrorStat.builder()
                .errorKey(errorKey)
                .count(1)
                .lastSeen(LocalDateTime.now())
                .sample(sample != null && sample.length() > 500 ? sample.substring(0, 500) : sample)
                .build())
        );
    }
}
