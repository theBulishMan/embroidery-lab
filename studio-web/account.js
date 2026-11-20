const $=s=>document.querySelector(s);
function status(text,error=false){$('#account-status').textContent=text;$('#account-status').dataset.error=String(error)}
async function api(body){const r=await fetch('/api/auth',body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const data=await r.json();if(r.status===401){location.replace('/login?next=account');throw new Error('请重新登录。')}if(!r.ok)throw new Error(data.error||'操作未完成。');return data}
$('#password-form').onsubmit=async e=>{e.preventDefault();if($('#new-password').value!==$('#confirm-password').value){status('两次新密码不一致，请检查。',true);return}$('#save-password').disabled=true;status('正在保存…');try{await api({action:'password',current_password:$('#current-password').value,new_password:$('#new-password').value});$('#password-form').reset();status('密码已更新。其他浏览器需要使用新密码登录。')}catch(e){status(e.message,true)}finally{$('#save-password').disabled=false}};
$('#logout').onclick=async()=>{try{await api({action:'logout'});location.replace('/login')}catch(e){status(e.message,true)}};
addEventListener('pageshow',e=>{if(e.persisted)location.reload()});
try{const {user}=await api();if(!user)location.replace('/login?next=account');else{$('#account-name').textContent=`${user.display_name} · ${user.username}`;$('#admin-link').hidden=user.role!=='admin'}}catch(e){status(e.message,true)}
