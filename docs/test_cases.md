# Automated Test Cases Catalog

Generated directly from `unittest` test discovery across `tests/`. Total automated test cases discovered: **73**.

| # | Module | Test Class | Test Method | Verification Summary | Result |
|---|---|---|---|---|---|
| 1 | `test_duration_priority` | `TestDurationExtractorAndMapping` | `test_01_gold_clear_duration_extraction_100_percent` | 01 gold clear duration extraction 100 percent | Pass (`unittest`) |
| 2 | `test_duration_priority` | `TestDurationExtractorAndMapping` | `test_02_gold_pure_duration_to_priority_mapping_100_percent` | 02 gold pure duration to priority mapping 100 percent | Pass (`unittest`) |
| 3 | `test_duration_priority` | `TestDurationExtractorAndMapping` | `test_03_gold_negative_recurring_and_ambiguous_cases` | 03 gold negative recurring and ambiguous cases | Pass (`unittest`) |
| 4 | `test_duration_priority` | `TestDurationExtractorAndMapping` | `test_04_patterns_per_language_at_least_three_each` | 04 patterns per language at least three each | Pass (`unittest`) |
| 5 | `test_duration_priority` | `TestDurationExtractorAndMapping` | `test_05_priority_precedence_unit_cases_and_monotonicity` | 05 priority precedence unit cases and monotonicity | Pass (`unittest`) |
| 6 | `test_duration_priority` | `TestPriorityRulesConfigServiceAndAging` | `test_01_yaml_config_change_takes_effect_immediately_and_audits` | 01 yaml config change takes effect immediately and audits | Pass (`unittest`) |
| 7 | `test_duration_priority` | `TestPriorityRulesConfigServiceAndAging` | `test_02_complaint_persists_duration_low_conf_review_and_continuous_feedback` | 02 complaint persists duration low conf review and continuous feedback | Pass (`unittest`) |
| 8 | `test_duration_priority` | `TestPriorityRulesConfigServiceAndAging` | `test_03_open_priority_aging_writes_system_audit` | 03 open priority aging writes system audit | Pass (`unittest`) |
| 9 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_01_pincode_lookup_cascading_and_submission_centroid_fallback` | 01 pincode lookup cascading and submission centroid fallback | Pass (`unittest`) |
| 10 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_02_admin_jurisdiction_isolation_district_and_department` | 02 admin jurisdiction isolation district and department | Pass (`unittest`) |
| 11 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_03_assign_task_dept_jurisdiction_matching_and_suggestion` | 03 assign task dept jurisdiction matching and suggestion | Pass (`unittest`) |
| 12 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_04_employee_restrictions_accept_after_photos_and_resolve` | 04 employee restrictions accept after photos and resolve | Pass (`unittest`) |
| 13 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_05_unassigned_beyond_threshold_escalates_to_super_admin` | 05 unassigned beyond threshold escalates to super admin | Pass (`unittest`) |
| 14 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_06_deactivate_admin_returns_open_complaints_and_preserves_history` | 06 deactivate admin returns open complaints and preserves history | Pass (`unittest`) |
| 15 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_07_admin_status_override_mandatory_reason_and_audit` | 07 admin status override mandatory reason and audit | Pass (`unittest`) |
| 16 | `test_hierarchy_jurisdiction` | `TestHierarchyAndJurisdiction` | `test_08_loader_reporting_mandal_handling_idempotency_and_public_suppression` | 08 loader reporting mandal handling idempotency and public suppression | Pass (`unittest`) |
| 17 | `test_ml` | `TestMLPhase1` | `test_01_model_comparison_stratified_group_artifacts_and_no_auto_activate` | 01 model comparison stratified group artifacts and no auto activate | Pass (`unittest`) |
| 18 | `test_ml` | `TestMLPhase1` | `test_02_classifier_prediction_emergency_override_and_explainability` | 02 classifier prediction emergency override and explainability | Pass (`unittest`) |
| 19 | `test_ml` | `TestMLPhase1` | `test_03_citizen_choice_preserved_mismatch_queued_and_admin_resolves` | 03 citizen choice preserved mismatch queued and admin resolves | Pass (`unittest`) |
| 20 | `test_ml` | `TestMLPhase1` | `test_04_offline_rule_fallback_when_no_model_artifact` | 04 offline rule fallback when no model artifact | Pass (`unittest`) |
| 21 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_01_citizen_submits_to_any_active_dept_and_saved_matches` | 01 citizen submits to any active dept and saved matches | Pass (`unittest`) |
| 22 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_02_nonexistent_or_deactivated_dept_raises_value_error` | 02 nonexistent or deactivated dept raises value error | Pass (`unittest`) |
| 23 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_03_only_users_can_submit_complaints` | 03 only users can submit complaints | Pass (`unittest`) |
| 24 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_04_priority_auto_detected_via_classify` | 04 priority auto detected via classify | Pass (`unittest`) |
| 25 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_05_image_upload_validation_limits_and_formats` | 05 image upload validation limits and formats | Pass (`unittest`) |
| 26 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_06_image_access_control_and_removal` | 06 image access control and removal | Pass (`unittest`) |
| 27 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_07_exif_gps_stripped_downscaled_and_persists` | 07 exif gps stripped downscaled and persists | Pass (`unittest`) |
| 28 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_08_no_district_rejected_no_auto_assign_and_suggested_employee` | 08 no district rejected no auto assign and suggested employee | Pass (`unittest`) |
| 29 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_09_employee_scope_claim_restricted_and_admin_reassign` | 09 employee scope claim restricted and admin reassign | Pass (`unittest`) |
| 30 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_10_sla_due_at_recalc_and_escalations` | 10 sla due at recalc and escalations | Pass (`unittest`) |
| 31 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_11_feedback_reopen_closing_remark_and_7day_autoclose` | 11 feedback reopen closing remark and 7day autoclose | Pass (`unittest`) |
| 32 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_12_performance_metrics_calculation` | 12 performance metrics calculation | Pass (`unittest`) |
| 33 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_13_user_management_security_and_regression` | 13 user management security and regression | Pass (`unittest`) |
| 34 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_14_migration_preserves_users_complaints_and_audit_history` | 14 migration preserves users complaints and audit history | Pass (`unittest`) |
| 35 | `test_permissions` | `TestCitizenGrievanceFullSuite` | `test_15_phase0_hardening_wards_and_synthetic_data` | 15 phase0 hardening wards and synthetic data | Pass (`unittest`) |
| 36 | `test_phase1_clusters` | `TestPhase1DuplicateClusters` | `test_01_radius_and_fallback_similarity_detection` | 01 radius and fallback similarity detection | Pass (`unittest`) |
| 37 | `test_phase1_clusters` | `TestPhase1DuplicateClusters` | `test_02_support_uniqueness_self_guard_and_threshold_escalation` | 02 support uniqueness self guard and threshold escalation | Pass (`unittest`) |
| 38 | `test_phase1_clusters` | `TestPhase1DuplicateClusters` | `test_03_merge_unmerge_jurisdiction_and_primary_resolution` | 03 merge unmerge jurisdiction and primary resolution | Pass (`unittest`) |
| 39 | `test_phase2_analytics` | `TestPhase2ForecastPredictionAnomaly` | `test_01_volume_forecast_shape_baseline_and_scope` | 01 volume forecast shape baseline and scope | Pass (`unittest`) |
| 40 | `test_phase2_analytics` | `TestPhase2ForecastPredictionAnomaly` | `test_02_resolution_time_model_beats_baseline_and_fallback` | 02 resolution time model beats baseline and fallback | Pass (`unittest`) |
| 41 | `test_phase2_analytics` | `TestPhase2ForecastPredictionAnomaly` | `test_03_anomaly_normal_silent_then_fires_on_spike_and_cleans_up` | 03 anomaly normal silent then fires on spike and cleans up | Pass (`unittest`) |
| 42 | `test_phase3_notifications` | `TestPhase3Notifications` | `test_01_email_validation_verification_reset_and_privacy_body` | 01 email validation verification reset and privacy body | Pass (`unittest`) |
| 43 | `test_phase3_notifications` | `TestPhase3Notifications` | `test_02_smtp_failure_records_retries_up_to_3_without_crashing` | 02 smtp failure records retries up to 3 without crashing | Pass (`unittest`) |
| 44 | `test_phase3_notifications` | `TestPhase3Notifications` | `test_03_all_complaint_events_inbox_and_preferences` | 03 all complaint events inbox and preferences | Pass (`unittest`) |
| 45 | `test_phase4_abuse_spam` | `TestPhase4AbuseAndSpam` | `test_01_gibberish_repeat_and_multilingual_abuse_flagged` | 01 gibberish repeat and multilingual abuse flagged | Pass (`unittest`) |
| 46 | `test_phase4_abuse_spam` | `TestPhase4AbuseAndSpam` | `test_02_daily_limit_and_cooldown_enforced` | 02 daily limit and cooldown enforced | Pass (`unittest`) |
| 47 | `test_phase4_abuse_spam` | `TestPhase4AbuseAndSpam` | `test_03_confirm_spam_blocks_user_and_only_super_admin_lifts_block` | 03 confirm spam blocks user and only super admin lifts block | Pass (`unittest`) |
| 48 | `test_phase5_selfservice` | `TestPhase5SelfService` | `test_01_forgot_password_reset_ttl_single_use_and_rate_limit` | 01 forgot password reset ttl single use and rate limit | Pass (`unittest`) |
| 49 | `test_phase5_selfservice` | `TestPhase5SelfService` | `test_02_withdraw_direct_vs_admin_reviewed_and_transfer` | 02 withdraw direct vs admin reviewed and transfer | Pass (`unittest`) |
| 50 | `test_phase5_selfservice` | `TestPhase5SelfService` | `test_03_internal_notes_hidden_from_citizens_and_other_staff` | 03 internal notes hidden from citizens and other staff | Pass (`unittest`) |
| 51 | `test_phase5_selfservice` | `TestPhase5SelfService` | `test_04_dpdp_export_and_account_anonymization` | 04 dpdp export and account anonymization | Pass (`unittest`) |
| 52 | `test_phase6_public_reports_backup` | `TestPhase6PublicReportsBackup` | `test_01_public_status_lookup_qr_and_rate_limit` | 01 public status lookup qr and rate limit | Pass (`unittest`) |
| 53 | `test_phase6_public_reports_backup` | `TestPhase6PublicReportsBackup` | `test_02_filtered_reports_csv_xlsx_pdf_audit_and_weekly_digest` | 02 filtered reports csv xlsx pdf audit and weekly digest | Pass (`unittest`) |
| 54 | `test_phase6_public_reports_backup` | `TestPhase6PublicReportsBackup` | `test_03_online_backup_encryption_rotation_and_restore` | 03 online backup encryption rotation and restore | Pass (`unittest`) |
| 55 | `test_security` | `TestSecurityAndPlatform` | `test_01_sql_injection_attempts_stay_inert` | 01 sql injection attempts stay inert | Pass (`unittest`) |
| 56 | `test_security` | `TestSecurityAndPlatform` | `test_02_direct_role_bypass_blocked` | 02 direct role bypass blocked | Pass (`unittest`) |
| 57 | `test_security` | `TestSecurityAndPlatform` | `test_03_idor_isolation_between_citizens` | 03 idor isolation between citizens | Pass (`unittest`) |
| 58 | `test_security` | `TestSecurityAndPlatform` | `test_04_cross_district_admin_isolation` | 04 cross district admin isolation | Pass (`unittest`) |
| 59 | `test_security` | `TestSecurityAndPlatform` | `test_05_employee_tampering_and_self_claim_disabled` | 05 employee tampering and self claim disabled | Pass (`unittest`) |
| 60 | `test_security` | `TestSecurityAndPlatform` | `test_06_malicious_file_uploads_rejected` | 06 malicious file uploads rejected | Pass (`unittest`) |
| 61 | `test_security` | `TestSecurityAndPlatform` | `test_07_brute_force_lockout_and_session_timeout` | 07 brute force lockout and session timeout | Pass (`unittest`) |
| 62 | `test_security` | `TestSecurityAndPlatform` | `test_08_public_status_tampering_and_rate_limit` | 08 public status tampering and rate limit | Pass (`unittest`) |
| 63 | `test_security` | `TestSecurityAndPlatform` | `test_09_xss_escaping_and_formula_injection_neutralized` | 09 xss escaping and formula injection neutralized | Pass (`unittest`) |
| 64 | `test_security` | `TestSecurityAndPlatform` | `test_10_otp_reset_reuse_expiry_and_database_abstraction_health` | 10 otp reset reuse expiry and database abstraction health | Pass (`unittest`) |
| 65 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_01_loader_idempotent_district_normalization_and_skipped_rows` | 01 loader idempotent district normalization and skipped rows | Pass (`unittest`) |
| 66 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_02_parent_filters_children_at_every_level` | 02 parent filters children at every level | Pass (`unittest`) |
| 67 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_03_pincode_auto_fill_and_server_side_mismatch_rejection` | 03 pincode auto fill and server side mismatch rejection | Pass (`unittest`) |
| 68 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_04_digipin_official_test_vectors_validation_and_faraway_flag` | 04 digipin official vectors validation and faraway flag | Pass (`unittest`) |
| 69 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_05_village_landmark_vs_empty_street_and_landmark_and_privacy_export` | 05 village landmark vs empty street and landmark and privacy export | Pass (`unittest`) |
| 70 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_06_location_correction_reroutes_district_and_audits` | 06 location correction reroutes district and audits | Pass (`unittest`) |
| 71 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_07_duplicate_detection_cascade_digipin_area_pincode` | 07 duplicate detection cascade digipin area pincode | Pass (`unittest`) |
| 72 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_08_migration_backfills_formatted_address_and_public_hides_pii` | 08 migration backfills formatted address and public hides pii | Pass (`unittest`) |
| 73 | `test_structured_address` | `TestStructuredAddressAndDigipin` | `test_09_streamlit_apptest_cascading_selects_and_pincode_autofill` | 09 streamlit appcascading selects and pincode autofill | Pass (`unittest`) |
