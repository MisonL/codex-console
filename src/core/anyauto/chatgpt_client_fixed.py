                    self._log(f"成功获取验证码: {otp_code}")
                tried_codes = {otp_code}
                for code_attempt in range(3):
                    success, next_state = self.verify_email_otp(otp_code, return_state=True)
                    if success:
                        otp_verified = True
                        state = next_state
                        self.last_registration_state = state
                        break
                    
                    self._log(f"验证码 {otp_code} 验证失败，可能已过期或由于延迟被覆盖，尝试重新收码...")
                    # 如果验证失败且还有时间，重新从邮箱捞一个最新的码
                    otp_code = skymail_client.wait_for_verification_code(email, timeout=20)
                    if not otp_code or otp_code in tried_codes:
                        return False, "验证码无效且未发现新码"
                    tried_codes.add(otp_code)
                
                if otp_verified:
                    continue
                return False, "验证码校验失败"

            # 其他常规状态跟随
            ok, next_state = self._follow_flow_state(state)
            if not ok:
                return False, f"状态流转失败: {next_state}"
            state = next_state
            
        return False, "注册流程超时或步骤过多"
