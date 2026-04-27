# NAS Git (Gitea) quick notes

NAS host: `192.168.1.7`  
SSH port: `2223`  
SSH user: `git`  

## 1) SSH key (you already generated it)

Generate:

```powershell
ssh-keygen -t ed25519 -C "root@ugreen.com"
```

Show public key:

```powershell
type "$env:USERPROFILE\.ssh\id_ed25519.pub"
```

Copy public key to clipboard:

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" | Set-Clipboard
```

Test SSH to NAS (this should work):

```powershell
ssh -p 2223 -T git@192.168.1.7
```

## 2) Add SSH key to NAS (Gitea web)

1. Open Gitea web UI
2. User settings / Preferences
3. **SSH Keys** → **Add Key**
4. Paste your `id_ed25519.pub` text

## 3) Create a repo (recommended: do it in the web UI)

In Gitea: **+ New Repository** → create (empty is fine).

Repo path example:
- Owner: `Crypter`
- Repo: `streaming-community`
- SSH URL: `ssh://git@192.168.1.7:2223/Crypter/streaming-community.git`

## 4) Put a project on the NAS (first time push)

Run inside your project folder:

```powershell
git init
git remote add origin "ssh://git@192.168.1.7:2223/<Owner>/<Repo>.git"
git add .
git commit -m "initial"
git push -u origin main
```

What each command means (simple):
- `git init`: make this folder a git project
- `git remote add origin ...`: link it to your NAS repo
- `git add .`: stage all files
- `git commit -m "initial"`: make a save point
- `git push -u origin main`: upload to NAS + remember this branch

If your branch is `master` instead of `main`:

```powershell
git push -u origin master
```

## 5) Save changes to NAS (every time after edits)

```powershell
git add .
git commit -m "update"
git push
```

## 6) See/check the remote URL

```powershell
git remote -v
```

If you need to change it:

```powershell
git remote set-url origin "ssh://git@192.168.1.7:2223/<Owner>/<Repo>.git"
```

## 7) Remove SSH keys (PC + NAS)

### Remove the key from the NAS (revoke access)
In Gitea web UI:
- User settings / Preferences → **SSH Keys**
- Delete the key you added

### Remove SSH key files from your PC
This deletes the key from your Windows user profile:

```powershell
del "$env:USERPROFILE\.ssh\id_ed25519"
del "$env:USERPROFILE\.ssh\id_ed25519.pub"
```

### Remove the saved host entry (optional)
If you want to clear the "known host" record:

```powershell
ssh-keygen -R "[192.168.1.7]:2223"
```

